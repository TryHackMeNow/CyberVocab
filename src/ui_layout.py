"""UI composition only. Business logic lives in vocabulary.py / suggest.py.

Builds the single-page NiceGUI interface for the CyberSec Vocabulary app:
a search bar with browser-style autocomplete, a collapsible "add new term"
pane, a scrollable results list with swipe-to-edit/remove, and import/
export/sanitize dialogs.

The module keeps two pieces of mutable, page-scoped state as module-level
globals — :data:`gui` (widget handles) and :data:`state` (UI state flags) —
plus a single shared :class:`~vocabulary.Vocabulary` instance, :data:`store`.
See the "file-globals" section below for details on why these exist as
globals rather than being passed around explicitly.
"""

import os
from nicegui import ui, run, app
from vocabulary import Vocabulary, ImportOptions, Team, VOCAB_DIR
from suggest import suggest_terms, get_definition
from datetime import datetime
from dataclasses import dataclass

#: Debounce (ms) applied to the search input before
#: :func:`handle_search_input_validation` fires, to avoid querying on
#: every single keystroke.
SUGGESTION_DEBOUNCE_MS = 150

# Developer-configurable: how many suggestions to show in the search bar's
# browser-style autocomplete dropdown when there is no vocabulary match.
SEARCH_SUGGESTION_LIMIT = 6

#: NiceGUI icon spec for the banner's shield logo, served from the static
#: ``/icons`` mount registered in :func:`build_ui`.
ICON_SHIELD_URL = 'img:/icons/shield-accept.svg'

#: Absolute filesystem path to the project's ``icons/`` directory, mounted
#: as static files under ``/icons`` so ``ICON_SHIELD_URL`` resolves.
_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")

@dataclass
class GuiElements:
    """Handles to the page's live NiceGUI widget instances.

    A single instance of this dataclass (:data:`gui`) is populated by the
    various ``build_*`` functions during :func:`build_ui` and then read
    and mutated by event handlers throughout the module. Centralizing the
    handles here avoids threading widget references through every
    function signature.

    Attributes:
        import_dialog: Dialog for importing a vocabulary file.
        radio_overwrite: Radio group choosing overwrite vs. preserve
            behavior for duplicate terms during import.
        radio_clear: Radio group choosing whether to clear the store
            before importing.
        sanitize_dialog: Dialog offering AI auto-fill for entries with
            missing fields (shown once at startup if applicable).
        search_input: The main search/lookup text input.
        search_suggestion_menu: Dropdown menu of browser-style term
            suggestions shown below the search input.
        add_pane_container: Container column for the "add/edit term"
            pane; toggled hidden/visible rather than added/removed.
        new_term_input: Term field within the add/edit pane.
        new_category_input: Category field within the add/edit pane.
        new_def_input: Definition textarea within the add/edit pane.
        stats_pane: Column showing the "N of M entries" counter.
        results_pane: Column listing the matching vocabulary entries.
    """

    import_dialog: ui.dialog
    radio_overwrite: ui.radio
    radio_clear: ui.radio
    sanitize_dialog: ui.dialog
    search_input: ui.input
    search_suggestion_menu: ui.menu
    add_pane_container: ui.column
    new_term_input: ui.input
    new_category_input: ui.input
    new_def_input: ui.input
    stats_pane: ui.column
    results_pane: ui.column

@dataclass
class GuiState:
    """Page-scoped UI state that isn't itself a widget.

    Tracks transient interaction state for the search/suggestion flow and
    the add-term pane's visibility, independent of the widgets that
    display it.

    Attributes:
        match_count: Number of vocabulary entries matching the current
            search query (last computed by :func:`update_results_pane`).
        search_suggestion_items: Current list of browser-style
            autocomplete suggestions shown under the search input.
        search_selected_index: Index into ``search_suggestion_items``
            currently highlighted via keyboard navigation, or -1 if none
            is selected.
        add_pane_visible: Whether the add/edit term pane is currently
            shown.
    """

    match_count: int
    search_suggestion_items: list[str]
    search_selected_index: int
    add_pane_visible: bool

# file-globals
#
# NiceGUI re-executes the page function (see main.index -> build_ui) once
# per connecting client, but this module's top-level code runs only once
# per process. These globals are placeholders populated by build_ui() for
# the *current* page render; in a single-user/single-page app like this
# one, that's sufficient, but it does mean state is not isolated per
# browser tab if multiple clients connect concurrently.

#: Live widget handles for the current page. Populated by the ``build_*``
#: functions called from :func:`build_ui`; fields are ``None`` until then.
gui = GuiElements(
    import_dialog=None,
    radio_overwrite=None,
    radio_clear=None,
    sanitize_dialog=None,
    search_input=None,
    search_suggestion_menu=None,
    add_pane_container=None,
    new_term_input=None,
    new_category_input=None,
    new_def_input=None,
    stats_pane=None,
    results_pane=None,
)

#: Current page-scoped interaction state. See :class:`GuiState`.
state = GuiState(
    match_count=0,
    search_suggestion_items=[],
    search_selected_index=-1,
    add_pane_visible=False,
)

#: The single shared vocabulary data store backing this app instance.
#: Loaded from disk at import time (see :class:`vocabulary.Vocabulary`).
store = Vocabulary()

def _team_color(team: str) -> str:
    """Map a :class:`vocabulary.Team` value to its display color.

    Args:
        team: A team value, expected to match one of :class:`Team`'s
            ``.value`` strings.

    Returns:
        str: A CSS color (hex string) for known teams, or ``""`` if
        ``team`` doesn't match any known value (callers treat an empty
        string as "no color override").
    """
    if team == Team.PURPLE.value:
        return "#D177FB"
    if team == Team.RED.value:
        return "#FF8181"
    if team == Team.BLUE.value:
        return "#60BCFA"
    return ""


# Import-dialog radio option labels (kept as named constants so the
# option lists and the comparisons against `.value` can't drift apart).
OVERWRITE_EXISTING = "Overwrite Existing"
PRESERVE_EXISTING = "Preserve Existing"
CLEAR_YES = "Yes"
CLEAR_NO = "No"


def num_suggestions() -> int:
    """Return how many search-bar autocomplete suggestions are currently shown."""
    return len(state.search_suggestion_items)


def get_app_css() -> str:
    """Read and return the contents of ``app.css`` next to this module.

    Returns:
        str: The full CSS file contents, intended to be injected via
        :func:`nicegui.ui.add_head_html` in :func:`build_ui`.
    """
    css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.css")
    with open(css_path, "r", encoding="utf-8") as f:
        return f.read()


def get_export_import_filename(token: str) -> str:
    """Build a timestamped filename for an export or import snapshot.

    Args:
        token: A short label identifying the operation, e.g. ``"export"``
            or ``"import"``; embedded in the filename for traceability.

    Returns:
        str: A filename of the form ``vocabulary_{token}_{YYMMDD_HHMMSS}.txt``.
    """
    timestamp = datetime.now().strftime("_%y%m%d_%H%M%S")
    filename = "vocabulary_" + token + timestamp + ".txt"
    return filename


async def fetch_definition(term: str) -> None:
    """Fetch a definition for ``term`` and populate the add-pane definition field.

    Shows an interim "Fetching…" placeholder in
    ``gui.new_def_input`` while the (blocking, network-bound) lookup runs
    on a worker thread via :func:`nicegui.run.io_bound`, then fills in the
    result. Notifies the user if no definition could be fetched, leaving
    the field empty for manual entry.

    Args:
        term: The term to fetch a definition for (as currently typed in
            the add-pane term field).
    """
    gui.new_def_input.set_value("Fetching definition for " + term + "…")
    definition = await run.io_bound(get_definition, term)
    gui.new_def_input.set_value(definition or "")
    if not definition:
        ui.notify(f'Could not fetch a definition for "{term}". Please enter one manually.', color="warning")


async def handle_search_input_validation(query: str):
    """Validation callback wired to the search input; drives result and suggestion updates.

    NiceGUI invokes this as the input's ``validation`` function on every
    (debounced) change. It is repurposed here as a general-purpose
    on-change hook rather than for actual validation: it always returns
    ``None`` (no validation error), and instead has the side effect of
    refreshing the results pane and, when the vocabulary itself has no
    match, fetching and showing browser-style term suggestions.

    Typing also always cancels any in-progress edit and hides the add
    pane, since editing and searching are mutually exclusive states.

    Args:
        query: The current search input value.

    Returns:
        None: Always returns ``None`` (no validation error is ever
        reported to the input widget).
    """
    # Typing into the search bar always ends an in-progress edit and hides the Add New Term pane
    set_add_pane_visible(False)
    # Input validator is called with debounce
    update_results_pane(query)
    # Only query for browser-style suggestions if the vocabulary itself has no match
    if state.match_count > 0:
        state.search_suggestion_items = []
        state.search_selected_index = -1
        update_search_suggestion_menu()
        return None
    state.search_suggestion_items = await run.io_bound(suggest_terms, query or "", SEARCH_SUGGESTION_LIMIT)
    state.search_selected_index = -1
    update_search_suggestion_menu()
    if num_suggestions() > 0:
        gui.search_suggestion_menu.open()
    return None


async def handle_search_keydown(e) -> None:
    """Keydown handler for the search input: drives suggestion-menu keyboard navigation.

    Handles:
        * ``Escape`` — clears suggestions and closes the add pane.
        * ``ArrowDown`` — moves focus into the add pane if it's already
          open, otherwise cycles the highlighted suggestion forward.
        * ``ArrowUp`` — cycles the highlighted suggestion backward.
        * ``Enter`` — commits the currently highlighted suggestion via
          :func:`select_search_suggestion`, if one is highlighted.

    Args:
        e: The NiceGUI keyboard event; ``e.args["key"]`` holds the key name.
    """
    key = e.args.get("key") if isinstance(e.args, dict) else None
    num_suggest = len(state.search_suggestion_items)
    if key == "Escape":
        state.search_suggestion_items = []
        state.search_selected_index = -1
        gui.search_suggestion_menu.close()
        set_add_pane_visible(False)
    elif key == "ArrowDown":
        if state.add_pane_visible:
            gui.new_term_input.run_method("focus")
        elif num_suggest > 0:
            state.search_selected_index = (state.search_selected_index + 1) % len(state.search_suggestion_items)
            update_search_suggestion_menu()
    elif key == "ArrowUp":
        if num_suggest > 0:
            state.search_selected_index = (state.search_selected_index - 1) % len(state.search_suggestion_items)
            update_search_suggestion_menu()
    elif key == "Enter":
        if state.search_selected_index >= 0 and state.search_selected_index < num_suggest:
            await select_search_suggestion(state.search_suggestion_items[state.search_selected_index])


async def handle_add_term_keyup(e) -> None:
    """Keyup handler for the add-pane term field.

    ``Escape``/``ArrowUp`` returns focus to the search input. ``Enter``
    (with a non-empty term) triggers a definition lookup via
    :func:`fetch_definition`.

    Args:
        e: The NiceGUI keyboard event; ``e.args["key"]`` holds the key name.
    """
    key = e.args.get("key") if isinstance(e.args, dict) else None
    if key == "Escape" or key == "ArrowUp":
        gui.search_input.run_method("focus")
    elif key == "Enter" and len(gui.new_term_input.value) > 0:
        await fetch_definition(gui.new_term_input.value)


async def handle_term_def_keyup(e) -> None:
    """Keyup handler for the add-pane category/definition fields.

    ``Escape``/``ArrowUp`` returns focus to the search input. ``Enter``
    (with a non-empty term) submits the entry via
    :func:`handle_add_entry`.

    Args:
        e: The NiceGUI keyboard event; ``e.args["key"]`` holds the key name.
    """
    key = e.args.get("key") if isinstance(e.args, dict) else None
    if key == "Escape" or key == "ArrowUp":
        gui.search_input.run_method("focus")
    elif key == "Enter" and len(gui.new_term_input.value) > 0:
        handle_add_entry(gui.new_term_input.value)


def update_search_suggestion_menu() -> None:
    """Rebuild the search suggestion dropdown from current state.

    Clears and repopulates ``gui.search_suggestion_menu`` with one label
    per item in ``state.search_suggestion_items``, highlighting the item
    at ``state.search_selected_index`` (if any). Closes the menu entirely
    when there are no suggestions to show. Each label is clickable and
    commits that suggestion via :func:`select_search_suggestion`.
    """
    gui.search_suggestion_menu.clear()
    if num_suggestions() == 0:
        gui.search_suggestion_menu.close()
        return
    with gui.search_suggestion_menu:
        for i, term in enumerate(state.search_suggestion_items):
            classes = "suggestion-item text-base"
            if i == state.search_selected_index:
                classes += " suggestion-item-active"
            ui.label(term).classes(classes).on("click", lambda e, t=term: select_search_suggestion(t))


async def select_search_suggestion(term: str) -> None:
    """Commit a chosen autocomplete suggestion as a new vocabulary entry.

    Adds ``term`` to the store with default metadata (via
    :meth:`vocabulary.Vocabulary.add`), clears the suggestion UI and
    search box, refreshes the results pane, and kicks off an async
    definition fetch (:func:`fill_definition_async`) so the new entry's
    definition populates shortly after.

    Args:
        term: The suggestion text the user selected.
    """
    state.search_suggestion_items = []
    state.search_selected_index = -1
    gui.search_suggestion_menu.close()
    try:
        store.add(term)
    except ValueError as exc:
        ui.notify(str(exc), color="negative")
        return
    ui.notify(f'Added "{term}". Fetching definition…', color="positive")
    gui.search_input.set_value("")
    update_results_pane("")
    await fill_definition_async(term)


async def fill_definition_async(term: str) -> None:
    """Background-fetch a definition for an already-added term and save it.

    Runs the (blocking) LLM lookup on a worker thread, then writes the
    result back via :meth:`vocabulary.Vocabulary.set_definition`. If the
    term was removed from the store while the lookup was in flight,
    ``set_definition`` returns False and this function exits quietly
    without refreshing the UI. On success, refreshes the results pane to
    show the new definition; on a failed/empty lookup, notifies the user
    that manual entry is needed.

    Args:
        term: The term whose definition should be fetched and persisted.
    """
    definition = await run.io_bound(get_definition, term)
    if not store.set_definition(term, definition or ""):
        return
    if not definition:
        ui.notify(f'Could not fetch a definition for "{term}". You can add one via right-swipe → Edit.', color="warning")
    update_results_pane(gui.search_input.value)


def handle_remove_entry(entry) -> bool:
    """Remove a vocabulary entry (e.g. from a swipe-to-remove action) and refresh the UI.

    Args:
        entry: The :class:`vocabulary.Entry` to remove (its ``.term`` is
            used as the store key).

    Returns:
        bool: True if the entry was removed; False if it was already
        gone. In both cases a corresponding notification is shown.
    """
    if store.remove(entry.term):
        ui.notify(f'Removed "{entry.term}".', color="positive")
        update_results_pane(gui.search_input.value)
        return True
    else:
        ui.notify(f'Failed to remove "{entry.term}".', color="negative")
        return False


def handle_edit_entry(entry, slide_item: ui.slide_item) -> bool:
    """Open the add/edit pane pre-filled with an existing entry's data.

    Resets the swipe gesture on ``slide_item``, marks the add pane as
    being in "edit" mode (so subsequent clear/cancel behavior knows an
    edit is in progress), populates the term/category/definition fields,
    and scrolls the page to the top so the pane is visible.

    Args:
        entry: The :class:`vocabulary.Entry` to load into the edit
            fields.
        slide_item: The swipeable row widget the edit action was
            triggered from; reset back to its neutral position.

    Returns:
        bool: Always True.
    """
    slide_item.reset()
    set_add_pane_visible(True)
    gui.new_term_input.set_value(entry.term)
    gui.new_category_input.set_value(entry.category)
    gui.new_def_input.set_value(entry.definition)
    ui.run_javascript('window.scrollTo(0, 0);')
    return True


def handle_clear_add_pane(slide_item: ui.slide_item) -> bool:
    """Reset the swipe gesture and clear the add/edit pane's input fields.

    Args:
        slide_item: The swipeable row widget the clear action was
            triggered from; reset back to its neutral position.

    Returns:
        bool: Always True.
    """
    slide_item.reset()
    gui.new_term_input.set_value("")
    gui.new_category_input.set_value("")
    gui.new_def_input.set_value("")
    return True
    

def handle_add_entry() -> bool:
    """Submit the add/edit pane's current fields as a new (or overwritten) entry.

    Reads ``term``/``definition``/``category`` from the corresponding
    ``gui`` input widgets, adds them to the store via
    :meth:`vocabulary.Vocabulary.add`, then clears and hides the pane and
    refreshes the (cleared) search results.

    Returns:
        bool: True on success; False if ``store.add`` rejected the input
        (e.g. empty term), in which case the pane is left open and
        populated so the user can correct it.
    """
    try:
        store.add(
            gui.new_term_input.value or "",
            gui.new_def_input.value or "",
            gui.new_category_input.value or "",
        )
    except ValueError as exc:
        ui.notify(str(exc), color="negative")
        return False
    # Clear input fields
    ui.notify(f'Saved "{gui.new_term_input.value}".', color="positive")
    gui.new_term_input.set_value("")
    gui.new_category_input.set_value("")
    gui.new_def_input.set_value("")
    set_add_pane_visible(False)
    gui.search_input.set_value("")
    update_results_pane("")
    return True


async def handle_import(e, dialog: ui.dialog) -> None:
    """Handle an uploaded vocabulary file: persist it, then import into the store.

    The uploaded bytes are first written to a timestamped file inside
    :data:`vocabulary.VOCAB_DIR` (so a server-side copy of every import is
    retained), then merged into the live store via
    :meth:`vocabulary.Vocabulary.import_from` using the options currently
    selected in the import dialog's radio buttons.

    Args:
        e: The NiceGUI upload event; ``e.file`` is the uploaded file
            handle (read asynchronously).
        dialog: The import dialog to close once the import completes.
    """
    # Get import options
    options = ImportOptions(gui.radio_overwrite.value == PRESERVE_EXISTING, gui.radio_clear.value == CLEAR_YES)
    # Write uploaded dictionary to dictionary folder
    filename = get_export_import_filename("import")
    server_filepath = os.path.join(VOCAB_DIR, filename)
    data = await e.file.read()
    with open(server_filepath, "wb") as f:
        f.write(data)
    # Import dictionary
    added, skipped = store.import_from(server_filepath, options)
    ui.notify(f"Imported {added} entries. Preserved {skipped} entries.", color="positive")
    dialog.close()
    update_results_pane("")


def handle_export() -> None:
    """Export the current vocabulary to a timestamped file and prompt the client to download it.

    Writes the export to :data:`vocabulary.VOCAB_DIR` via
    :meth:`vocabulary.Vocabulary.export_to`, then triggers a browser
    download of that same server-side file via :func:`nicegui.ui.download`.
    """
    # Write vocabulary to vocabulary folder
    filename = get_export_import_filename("export")
    server_filepath = os.path.join(VOCAB_DIR, filename)
    store.export_to(server_filepath)
    # Download to client (show download dialog)
    ui.download(server_filepath, filename)


def build_import_dialog() -> None:
    """Construct the "Import Vocabulary" dialog and store it in ``gui.import_dialog``.

    The dialog offers two radio choices (duplicate-handling and
    clear-before-import) plus a ``.txt`` file upload control that, on
    upload, triggers :func:`handle_import`. The dialog itself is *not*
    opened here; callers open it on demand (see the banner's menu item).
    """
    with ui.dialog() as dialog, ui.column().classes("panel"):
        ui.label("Import Vocabulary (.txt)").classes("text-lg w-fit, mb-8")
        ui.label("How do you want to handle duplicate entries?").classes("w-fit")
        gui.radio_overwrite = ui.radio([OVERWRITE_EXISTING, PRESERVE_EXISTING], value=PRESERVE_EXISTING).classes("gap-1")
        ui.label("Do you want to clear the vocabulary before importing?").classes("w-fit")
        gui.radio_clear = ui.radio([CLEAR_YES, CLEAR_NO], value=CLEAR_NO).classes("gap-1")
        ui.upload(on_upload=lambda e: handle_import(e, dialog), max_files=1).classes("w-full").props(
            "accept=.txt"
        )
    gui.import_dialog = dialog


async def handle_sanitize_confirm(do_sanitize: bool, dialog: ui.dialog) -> None:
    """Handle the user's response to the startup "sanitize incomplete entries?" prompt.

    If declined, simply closes the dialog. If accepted, iterates over all
    entries with missing fields (see
    :meth:`vocabulary.Vocabulary.entries_missing_fields`) that still lack
    a definition, fetches one via the LLM for each, and persists any that
    succeed. Refreshes the results pane and reports how many entries were
    fixed out of how many were incomplete.

    Args:
        do_sanitize: Whether the user chose to proceed with sanitization.
        dialog: The confirmation dialog to close.
    """
    dialog.close()
    if not do_sanitize:
        return
    entries = store.entries_missing_fields()
    fixed = 0
    for entry in entries:
        if (entry.definition or "").strip():
            continue
        definition = await run.io_bound(get_definition, entry.term)
        if store.set_definition(entry.term, definition or ""):
            fixed += 1
    update_results_pane(gui.search_input.value if gui.search_input else "")
    ui.notify(f"Sanitized {fixed} of {len(entries)} entries with missing fields.", color="positive")


def build_sanitize_dialog() -> None:
    """Build and immediately open a one-time prompt to auto-fill incomplete entries, if any exist.

    Queries :meth:`vocabulary.Vocabulary.entries_missing_fields`; if the
    store has no incomplete entries, this is a no-op (no dialog is built
    or shown). Otherwise builds a confirmation dialog whose Yes/No buttons
    both route through :func:`handle_sanitize_confirm`, then opens it.
    """
    incomplete = store.entries_missing_fields()
    if not incomplete:
        return
    with ui.dialog() as dialog, ui.column().classes("panel"):
        ui.label("Incomplete Vocabulary Entries").classes("text-lg w-fit mb-8")
        ui.label(
            f'{len(incomplete)} of {len(store.entries)} entries have missing fields. '
            'Sanitize them automatically using AI?'
        ).classes("w-fit")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("No", on_click=lambda: handle_sanitize_confirm(False, dialog)).props("flat")
            ui.button("Yes, sanitize", on_click=lambda: handle_sanitize_confirm(True, dialog)).props("color=primary")
    gui.sanitize_dialog = dialog
    dialog.open()


def build_banner() -> None:
    """Build the top banner: logo, title, and the export/import menu button."""
    with ui.row().classes("w-full banner"):
        with ui.row().classes("items-center"):
            ui.icon(ICON_SHIELD_URL).classes("w-10 h-10 shrink-0")
            with ui.column().classes("gap-0"):
                ui.label("CyberSec Vocabulary").classes(
                    "text-xl font-semibold text-white"
                )
                ui.label("Terminology Reference").classes("text-sm text-muted")
        with ui.button(icon="menu").props("flat color=white"):
            with ui.menu():
                ui.menu_item("Export vocabulary", on_click=lambda: handle_export())
                ui.menu_item("Import vocabulary", on_click=lambda: gui.import_dialog.open())


def build_search_pane() -> None:
    """Build the search bar and its attached (initially-empty) suggestion menu.

    Wires :func:`handle_search_input_validation` as the input's debounced
    validation callback and :func:`handle_search_keydown` as its keydown
    handler. Stores widget handles on ``gui.search_input`` and
    ``gui.search_suggestion_menu``.
    """
    with ui.column().classes("w-full gap-0 relative"):
        with ui.row().classes("panel w-full accent-border-color search-bar"):
            gui.search_input = (
                ui.input(placeholder="Search Vocabulary (e.g. Malware, Phishing...)",
                         validation = handle_search_input_validation)
                .classes("grow")
                .props(f"borderless dense hide-bottom-space debounce={SUGGESTION_DEBOUNCE_MS}")
            )
            with gui.search_input.add_slot("prepend"):
                ui.icon("search").classes("")
            with ui.menu().props("fit no-focus").classes(
                "font-mono panel accent-border-color search-suggestion-menu dense"
            ) as menu:
                gui.search_suggestion_menu = menu
        gui.search_input.on("keydown", handle_search_keydown)


def build_add_pane() -> None:
    """Build the (initially hidden) add/edit term pane.

    Contains the term, category, and definition inputs plus a swipeable
    wrapper offering a "Clear" action from either swipe direction (see
    :func:`handle_clear_add_pane`). Keyup handlers route ``Enter``/
    ``Escape``/``ArrowUp`` through :func:`handle_add_term_keyup` and
    :func:`handle_term_def_keyup`. Visibility is toggled later via
    :func:`set_add_pane_visible`, not by destroying/rebuilding this pane.
    """
    gui.add_pane_container = ui.column().classes("w-full hidden")
    with gui.add_pane_container:
        with ui.slide_item().classes("bg-color w-full") as slide_item:
            slide_item.right('Clear', color='blue', on_slide=lambda e: handle_clear_add_pane(slide_item))
            slide_item.left('Clear', color='blue', on_slide=lambda e: handle_clear_add_pane(slide_item))
            with ui.column().classes("panel w-full accent-border-color gap-0"):
                # Add input fields
                gui.new_term_input = ui.input(placeholder="Add New Term…").classes("w-full").props("borderless")
                gui.new_category_input = ui.input(placeholder="Category…").classes("w-full").props("borderless")
                gui.new_def_input = ui.textarea(placeholder="Definition…").classes("w-full").props("borderless autogrow")
                # Add event handlers
                gui.new_term_input.on("keyup", lambda e: handle_add_term_keyup(e))
                gui.new_def_input.on("keyup", lambda e: handle_term_def_keyup(e))
                gui.new_category_input.on("keyup", lambda e: handle_term_def_keyup(e))
    

def build_term_frame(entry) -> None:
    """Build one swipeable result row for a single vocabulary entry.

    Swiping right triggers removal (:func:`handle_remove_entry`); swiping
    left opens the entry for editing (:func:`handle_edit_entry`). The
    entry's category pill is tinted using :func:`_team_color` when its
    team maps to a known color.

    Args:
        entry: The :class:`vocabulary.Entry` to render.
    """
    with ui.slide_item().classes("w-full bg-color") as slide_item:
        slide_item.right('Remove', color='red', on_slide=lambda e: handle_remove_entry(entry))
        slide_item.left('Edit', color='blue', on_slide=lambda e: handle_edit_entry(entry, slide_item))
        with ui.column().classes("panel"):
            with ui.row().classes("items-center"):
                ui.label(entry.term).classes("text-mono text-base")
                category_label = ui.label(entry.category).classes("pill")
                team_color = _team_color(entry.team)
                if team_color != "":
                    category_label.style(f"color:{team_color}")
            ui.label(entry.definition).classes("text-term-def-color text-mono")


def set_add_pane_visible(visible: bool) -> None:
    """Show or hide the add/edit pane and keep ``state.add_pane_visible`` in sync.

    Toggles the ``hidden`` CSS class on ``gui.add_pane_container`` rather
    than mounting/unmounting it, so its input values persist across
    visibility changes.

    Args:
        visible: True to show the pane, False to hide it.
    """
    state.add_pane_visible = visible
    if visible:
        gui.add_pane_container.classes(remove="hidden")
    else:
        gui.add_pane_container.classes(add="hidden")


def update_results_pane(query: str) -> None:
    """Recompute search matches for ``query`` and redraw the stats and results panes.

    Updates ``state.match_count`` as a side effect (consumed by
    :func:`handle_search_input_validation` to decide whether to also show
    autocomplete suggestions).

    Args:
        query: The current search query; forwarded to
            :meth:`vocabulary.Vocabulary.find_matches`. An empty string
            lists every entry.
    """
    # Clear the pane
    gui.results_pane.clear()
    gui.stats_pane.clear()
    matches = store.find_matches(query)
    state.match_count = len(matches)
    total_count = len(store.entries)
    # Build stats pane
    with gui.stats_pane:
        with ui.row().classes("w-full items-right"):
            ui.space()
            ui.label(f"{state.match_count} of {total_count} entries").classes(
                "text-default-color pill text-muted"
            )
    # Build results pane
    with gui.results_pane:
        if not matches:
            ui.label("No matches.").classes("text-base text-mono")
        for entry in matches:
            build_term_frame(entry)


def build_ui() -> None:
    """Construct the entire page: styles, dialogs, banner, search, add pane, and results.

    Called once per client connection from :func:`main.index`. Order of
    operations matters here: the import dialog and banner are built
    first, then the main layout column (search pane, add pane, stats and
    results placeholders), after which the results pane is populated for
    an empty query and the startup sanitize dialog is built/opened last
    (so it appears on top of an already-rendered page).
    """
    ui.add_head_html(f"<style>{get_app_css()}</style>")
    app.add_static_files('/icons', _ICONS_DIR)
    build_import_dialog()
    build_banner()
    with ui.column().classes("w-full items-stretch gap-4"):
        build_search_pane()
        build_add_pane()
        gui.stats_pane = ui.column().classes("w-full")
        gui.results_pane = ui.column().classes("w-full")

    update_results_pane("")
    build_sanitize_dialog()

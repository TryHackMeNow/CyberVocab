"""UI composition only. Business logic lives in vocabulary.py / suggest.py."""

import os
from nicegui import ui, run, app
from vocabulary import Vocabulary, ImportOptions, Team, VOCAB_DIR
from suggest import suggest_terms, get_definition
from datetime import datetime
from dataclasses import dataclass

SUGGESTION_DEBOUNCE_MS = 150

# Developer-configurable: how many suggestions to show in the search bar's
# browser-style autocomplete dropdown when there is no vocabulary match.
SEARCH_SUGGESTION_LIMIT = 6

ICON_SHIELD_URL = 'img:/icons/shield-accept.svg'
_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")

@dataclass
class GuiElements:
    import_dialog: ui.dialog
    radio_overwrite: ui.radio
    radio_clear: ui.radio
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
    match_count: int
    search_suggestion_items: list[str]
    search_selected_index: int
    add_pane_visible: bool
    add_pane_forced_by_edit: bool

# file-globals
gui = GuiElements(
    import_dialog=None,
    radio_overwrite=None,
    radio_clear=None,
    search_input=None,
    search_suggestion_menu=None,
    add_pane_container=None,
    new_term_input=None,
    new_category_input=None,
    new_def_input=None,
    stats_pane=None,
    results_pane=None,
)

state = GuiState(
    match_count=0,
    search_suggestion_items=[],
    search_selected_index=-1,
    add_pane_visible=False,
    add_pane_forced_by_edit=False,
)
store = Vocabulary()

def _team_color(team: str) -> str:
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
    return len(state.search_suggestion_items)


def get_app_css() -> str:
    css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.css")
    with open(css_path, "r", encoding="utf-8") as f:
        return f.read()


def get_export_import_filename(token: str) -> str:
    timestamp = datetime.now().strftime("_%y%m%d_%H%M%S")
    filename = "vocabulary_" + token + timestamp + ".txt"
    return filename


async def fetch_definition(term: str) -> None:
    gui.new_def_input.set_value("Fetching definition for " + term + "…")
    definition = await run.io_bound(get_definition, term)
    gui.new_def_input.set_value(definition or "")
    if not definition:
        ui.notify(f'Could not fetch a definition for "{term}". Please enter one manually.', color="warning")


async def handle_search_input_validation(query: str):
    # Input validator is called with debounce
    print(query)
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
    key = e.args.get("key") if isinstance(e.args, dict) else None
    if not state.search_suggestion_items:
        return
    elif key == "ArrowDown":
        state.search_selected_index = (state.search_selected_index + 1) % len(state.search_suggestion_items)
        update_search_suggestion_menu()
    elif key == "ArrowUp":
        state.search_selected_index = (state.search_selected_index - 1) % len(state.search_suggestion_items)
        update_search_suggestion_menu()
    elif key == "Escape":
        state.search_suggestion_items = []
        state.search_selected_index = -1
        gui.search_suggestion_menu.close()
    elif key == "Enter" and state.search_selected_index >= 0:
        select_search_suggestion(state.search_suggestion_items[state.search_selected_index])


def update_search_suggestion_menu() -> None:
    gui.search_suggestion_menu.clear()
    if num_suggestions() == 0:
        gui.search_suggestion_menu.close()
        return
    with gui.search_suggestion_menu:
        for i, term in enumerate(state.search_suggestion_items):
            classes = "suggestion-item"
            if i == state.search_selected_index:
                classes += " suggestion-item-active"
            ui.label(term).classes(classes).on("click", lambda e, t=term: select_search_suggestion(t))


def select_search_suggestion(term: str) -> None:
    gui.new_term_input.set_value(term)
    state.search_suggestion_items = []
    state.search_selected_index = -1
    gui.search_suggestion_menu.close()
    update_results_pane(term)
    gui.new_term_input.run_method("focus")


def handle_remove_entry(entry) -> bool:
    if store.remove(entry.term):
        ui.notify(f'Removed "{entry.term}".', color="positive")
        update_results_pane(gui.search_input.value)
        return True
    else:
        ui.notify(f'Failed to remove "{entry.term}".', color="negative")
        return False


def handle_edit_entry(entry, slide_item: ui.slide_item) -> bool:
    slide_item.reset()
    state.add_pane_forced_by_edit = True
    set_add_pane_visible(True)
    gui.new_term_input.set_value(entry.term)
    gui.new_category_input.set_value(entry.category)
    gui.new_def_input.set_value(entry.definition)
    ui.run_javascript('window.scrollTo(0, 0);')
    return True


def handle_clear_add_pane(slide_item: ui.slide_item) -> bool:
    slide_item.reset()
    gui.new_term_input.set_value("")
    gui.new_category_input.set_value("")
    gui.new_def_input.set_value("")
    state.add_pane_forced_by_edit = False
    update_results_pane(gui.search_input.value or "")
    return True
    

def handle_add_entry() -> bool:
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
    state.add_pane_forced_by_edit = False
    gui.search_input.set_value("")
    update_results_pane("")
    return True


async def handle_import(e, dialog: ui.dialog) -> None:
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
    # Write vocabulary to vocabulary folder
    filename = get_export_import_filename("export")
    server_filepath = os.path.join(VOCAB_DIR, filename)
    store.export_to(server_filepath)
    # Download to client (show download dialog)
    ui.download(server_filepath, filename)


def build_import_dialog() -> None:
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


def build_banner() -> None:
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
    with ui.column().classes("w-full gap-0 relative"):
        with ui.row().classes("panel w-full accent-border-color search-bar"):
            gui.search_input = (
                ui.input(placeholder="Search Vocabulary (e.g. Malware, Phishing...)",
                         validation = handle_search_input_validation)
                .classes("grow dense")
                .props(f"borderless dense debounce={SUGGESTION_DEBOUNCE_MS}")
            )
            with gui.search_input.add_slot("prepend"):
                ui.icon("search").classes("")
            with ui.menu().props("fit no-focus").classes(
                "font-mono panel accent-border-color search-suggestion-menu dense"
            ) as menu:
                gui.search_suggestion_menu = menu
        gui.search_input.on("keydown", handle_search_keydown)


def build_add_pane() -> None:
    gui.add_pane_container = ui.column().classes("w-full hidden")
    with gui.add_pane_container:
        with ui.slide_item().classes("bg-color w-full") as slide_item:
            slide_item.right('Clear', color='blue', on_slide=lambda e: handle_clear_add_pane(slide_item))
            slide_item.left('Clear', color='blue', on_slide=lambda e: handle_clear_add_pane(slide_item))
            with ui.column().classes("panel w-full accent-border-color"):
                with ui.row().classes("w-full items-stretch"):
                    gui.new_term_input = ui.input(placeholder="Add New Term…").classes("w-full").props("borderless autogrow")
                    gui.new_category_input = ui.input(placeholder="Category…").classes("grow").props("borderless autogrow")
                gui.new_def_input = ui.textarea(placeholder="Definition…").classes("w-full grow").props("borderless autogrow")
                # Add event handlers
                gui.new_term_input.on("keyup.enter", lambda e: fetch_definition(gui.new_term_input.value))
                gui.new_def_input.on("keyup.enter", lambda e: handle_add_entry())
    

def build_term_frame(entry) -> None:
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


def set_add_pane_visible(visible: bool, *, prefill_term: str | None = None) -> None:
    """Shows or hides the Add New Term pane. The pane is shown only when
    there is no vocabulary match for the current search (in which case
    `prefill_term` carries over what the user already typed) or while
    editing an entry via slide-left (state.add_pane_forced_by_edit)."""
    state.add_pane_visible = visible
    if visible:
        gui.add_pane_container.classes(remove="hidden")
        if prefill_term is not None and not (gui.new_term_input.value or "").strip():
            gui.new_term_input.set_value(prefill_term)
    else:
        gui.add_pane_container.classes(add="hidden")


def update_results_pane(query: str) -> None:
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

    # Show the Add New Term pane only when there's no match for a non-empty
    # query (and we're not already showing it because of an in-progress
    # edit, which takes precedence and shouldn't be dismissed by this).
    if not state.add_pane_forced_by_edit:
        if state.match_count == 0 and (query or "").strip():
            set_add_pane_visible(True, prefill_term=query)
        else:
            set_add_pane_visible(False)

def build_ui() -> None:
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

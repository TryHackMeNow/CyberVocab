
"""Application entry point.

Wires up the single NiceGUI page route and starts the NiceGUI server. All
actual UI construction is delegated to :func:`ui_layout.build_ui`; this
module's only job is process bootstrap.

Run with::

    python3 main.py

"""

from nicegui import ui
from ui_layout import build_ui


@ui.page("/")
def index() -> None:
    """Render the application's single page.

    Registered as the handler for the root route ("/"). Delegates all
    layout and widget construction to :func:`ui_layout.build_ui`, which is
    re-invoked for each new client connection (NiceGUI's per-client page
    model), giving every browser tab/session its own set of UI elements.
    """
    build_ui()


# Start the NiceGUI web server. ``dark=True`` enables dark mode by default;
# ``reload=False`` disables the dev auto-reloader so the process behaves
# consistently when run directly (e.g. in a packaged/production context).
ui.run(title="CyberSec Vocabulary", dark=True, reload=False)

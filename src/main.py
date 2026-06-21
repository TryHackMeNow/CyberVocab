
from nicegui import ui
from ui_layout import build_ui


@ui.page("/")
def index() -> None:
    build_ui()


ui.run(title="CyberSec Vocabulary", dark=True, reload=False)

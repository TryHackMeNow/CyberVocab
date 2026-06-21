"""Pure data logic: vocabulary persistence and lookup. No UI code here."""

import os
from dataclasses import dataclass
from enum import Enum
from typing import TextIO

# Anchor the vocabulary directory to the project root (one level above this
# file, which lives in src/) rather than to the process's current working
# directory. This way the app finds vocabulary.txt regardless of whether it
# is launched as `python3 main.py` from within src/ or `python3 src/main.py`
# from the project root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOCAB_DIR = os.path.join(_PROJECT_ROOT, "vocabulary")
VOCAB_FILENAME = "vocabulary.txt"
VOCAB_FILEPATH = os.path.join(VOCAB_DIR, VOCAB_FILENAME)
SEPARATOR = " :: "
DEFAULT_CATEGORY = "General"
DEFAULT_ADOPTION = "Adopted"


class Team(str, Enum):
    PURPLE = "Purple"
    RED = "Red"
    BLUE = "Blue"


DEFAULT_TEAM = Team.PURPLE.value

@dataclass
class Entry:
    term: str
    category: str
    adoption: str
    team: str
    definition: str

@dataclass
class ImportOptions:
    preserve_existing: bool
    clear_before: bool

class Vocabulary:

    def __init__(self, path: str = VOCAB_FILEPATH):
        self.path = path
        self.entries: dict[str, Entry] = {}
        self.load()
        
    def get_entry(self, term: str) -> "Entry | None":
        return self.entries.get(term)

    def load(self) -> None:
        self.entries.clear()
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parsed = self._parse_line(line)
                if parsed:
                    self.entries[parsed.term] = parsed

    @staticmethod
    def _parse_line(line: str) -> "Entry | None":
        parts = line.split(SEPARATOR)
        if len(parts) == 5:
            term, category, adoption, team, definition, = parts
        elif len(parts) == 2:
            term, definition = parts
            category = DEFAULT_CATEGORY
            adoption = DEFAULT_ADOPTION
            team = DEFAULT_TEAM
        else:
            return None
        # Strip the input
        term = term.strip()
        if not term:
            return None
        return Entry(
            term=term,
            category=category.strip() or DEFAULT_CATEGORY,
            adoption=adoption or DEFAULT_ADOPTION,
            team=team or DEFAULT_TEAM,
            definition=definition.strip(),
        )

    def write_entry(self, f: TextIO, e: Entry) -> None:
        f.write(f"{e.term}{SEPARATOR}{e.category}{SEPARATOR}{e.adoption}{SEPARATOR}{e.team}{SEPARATOR}{e.definition}\n")

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# CyberSec Vocabulary — Term :: Category :: Adoption :: Team :: Definition\n")
            for term in sorted(self.entries, key=str.lower):
                e = self.entries[term]
                self.write_entry(f, e)

    def remove(self, term: str) -> bool:
        if term in self.entries:
            del self.entries[term]
            self.save()
            return True
        else:
            return False

    def add(self, term: str, definition: str, category: str = DEFAULT_CATEGORY, adoption: str = DEFAULT_ADOPTION, team: str = DEFAULT_TEAM) -> None:
        term, definition, category, adoption, team = (
            term.strip(),
            definition.strip(),
            (category.strip() or DEFAULT_CATEGORY),
            (adoption.strip() or DEFAULT_ADOPTION),
            (team.strip() or DEFAULT_TEAM),
        )
        if not term or not definition:
            raise ValueError("Term and definition are required.")
        self.entries[term] = Entry(term=term, category=category, adoption=adoption, team=team, definition=definition)
        self.save()

    def export_to(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            for term in sorted(self.entries, key=str.lower):
                e = self.entries[term]
                self.write_entry(f, e)

    def import_from(self, filepath: str, options: ImportOptions) -> tuple[int, int]:
        num_added = 0
        num_skipped = 0
        if options.clear_before:
            self.entries.clear()
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parsed = self._parse_line(line)
                if parsed:
                    if parsed.term in self.entries:
                        if options.preserve_existing:
                            num_skipped += 1
                        else:
                            self.entries[parsed.term] = parsed
                            num_added += 1
                    else:
                        self.entries[parsed.term] = parsed
                        num_added += 1
        self.save()
        return num_added, num_skipped

    def find_matches(self, query: str) -> list[Entry]:
        if len(query) == 0:
            return [self.entries[t] for t in sorted(self.entries, key=str.lower)]
        q = query.lower()
        starts_with = sorted(
            [t for t in self.entries if t.lower().startswith(q)], key=str.lower
        )
        contains = sorted(
            [t for t in self.entries if q in t.lower() and t not in starts_with],
            key=str.lower,
        )
        return [self.entries[t] for t in starts_with + contains]
    
# End class Vocabulary
    

"""Pure data logic: vocabulary persistence and lookup. No UI code here.

This module owns the on-disk vocabulary format and the in-memory
:class:`Vocabulary` store used by the rest of the application. The file
format is a simple line-oriented, ``::``-delimited text file (see
``SEPARATOR``) so it stays human-readable and diff-friendly in version
control.
"""

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

#: Absolute path to the directory containing the vocabulary data file and
#: any exported/imported snapshots.
VOCAB_DIR = os.path.join(_PROJECT_ROOT, "vocabulary")

#: Filename (no directory) of the default vocabulary data file.
VOCAB_FILENAME = "vocabulary.txt"

#: Absolute path to the default vocabulary data file, used as the default
#: argument to :class:`Vocabulary`.
VOCAB_FILEPATH = os.path.join(VOCAB_DIR, VOCAB_FILENAME)

#: Field delimiter used in the on-disk vocabulary format. Chosen to be
#: unlikely to appear naturally inside a term or definition.
SEPARATOR = " :: "

#: Category assigned to entries that don't specify one.
DEFAULT_CATEGORY = "General"

#: Adoption status assigned to entries that don't specify one.
DEFAULT_ADOPTION = "Adopted"


class Team(str, Enum):
    """Color-coded security team an entry can be associated with.

    Mirrors the conventional red/blue/purple team split used in security
    exercises. Inherits from ``str`` so instances compare equal to their
    plain string ``.value`` and serialize naturally to the on-disk format.
    """

    PURPLE = "Purple"
    RED = "Red"
    BLUE = "Blue"


#: Team assigned to entries that don't specify one.
DEFAULT_TEAM = Team.PURPLE.value

@dataclass
class Entry:
    """A single vocabulary record.

    Attributes:
        term: The vocabulary word/phrase itself; acts as the unique key
            within a :class:`Vocabulary`.
        category: Free-text grouping label (e.g. "Network", "Malware").
        adoption: Free-text adoption/status label (e.g. "Adopted",
            "Proposed").
        team: One of :class:`Team`'s values, indicating which team the
            term is most associated with.
        definition: The human-readable definition text.
    """

    term: str
    category: str
    adoption: str
    team: str
    definition: str

@dataclass
class ImportOptions:
    """Options controlling how :meth:`Vocabulary.import_from` merges data.

    Attributes:
        preserve_existing: If True, entries already present in the store
            are kept as-is and the incoming duplicate is skipped instead
            of overwriting it.
        clear_before: If True, the in-memory store is emptied before the
            import is applied, so the result is exactly the imported file
            (subject to ``preserve_existing``, which is moot in this case
            since nothing pre-exists).
    """

    preserve_existing: bool
    clear_before: bool

class Vocabulary:
    """In-memory vocabulary store backed by a flat text file.

    Entries are keyed by their ``term`` (case-sensitive key, though
    lookups and sorting are generally case-insensitive) and held in an
    ordinary ``dict``. Every mutating method that changes persisted state
    (:meth:`add`, :meth:`remove`, :meth:`set_definition`, :meth:`save`,
    :meth:`import_from`) writes the full store back to disk immediately,
    trading write efficiency for simplicity and crash-safety.
    """

    def __init__(self, path: str = VOCAB_FILEPATH):
        """Create a store and load any existing entries from disk.

        Args:
            path: Path to the backing text file. Defaults to
                :data:`VOCAB_FILEPATH`. If the file does not exist yet,
                the store simply starts empty (see :meth:`load`).
        """
        self.path = path
        self.entries: dict[str, Entry] = {}
        self.load()
        
    def get_entry(self, term: str) -> "Entry | None":
        """Look up a single entry by its exact term.

        Args:
            term: The exact (case-sensitive) term key to look up.

        Returns:
            Entry | None: The matching entry, or ``None`` if no entry
            with that exact term exists.
        """
        return self.entries.get(term)

    def load(self) -> None:
        """(Re)populate ``self.entries`` from the backing file.

        Clears any in-memory entries first. Blank lines and lines
        starting with ``#`` (comments) are skipped. If the backing file
        does not exist, the store is left empty rather than raising.
        """
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
        """Parse one data line into an :class:`Entry`.

        Accepts two line shapes, split on :data:`SEPARATOR`:

        * 5 fields: ``term :: category :: adoption :: team :: definition``
          (the full, current format).
        * 2 fields: ``term :: definition`` (a legacy/shorthand format),
          where ``category``, ``adoption``, and ``team`` fall back to
          their respective defaults.

        Any other field count is treated as malformed and ignored.

        Args:
            line: A single, already newline-stripped line of the file
                (not a comment or blank line).

        Returns:
            Entry | None: The parsed entry, or ``None`` if the line has
            an unsupported number of fields or the term is empty after
            stripping.
        """
        parts = line.split(SEPARATOR)
        if len(parts) == 5:
            term, category, adoption, team, definition, = parts
        elif len(parts) == 4:
            term, category, team, definition, = parts
            adoption = DEFAULT_ADOPTION
        elif len(parts) == 2:
            term, definition = parts
            category = DEFAULT_CATEGORY
            adoption = DEFAULT_ADOPTION
            team = DEFAULT_TEAM
        else:
            print("Vocabulary has unsupported format. Entry should be 'term :: category :: [adoption] :: team :: definition'.")
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
        """Write a single entry as one :data:`SEPARATOR`-delimited line.

        Args:
            f: An already-open writable text file handle.
            e: The entry to serialize.
        """
        f.write(f"{e.term}{SEPARATOR}{e.category}{SEPARATOR}{e.adoption}{SEPARATOR}{e.team}{SEPARATOR}{e.definition}\n")

    def save(self) -> None:
        """Persist all current entries to ``self.path``, sorted by term.

        Overwrites the file completely (not an incremental append),
        prefixed with a single comment line documenting the column
        layout. Sorting is case-insensitive for stable, readable diffs.
        """
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# CyberSec Vocabulary — Term :: Category :: Adoption :: Team :: Definition\n")
            for term in sorted(self.entries, key=str.lower):
                e = self.entries[term]
                self.write_entry(f, e)

    def remove(self, term: str) -> bool:
        """Delete an entry by term and persist the change.

        Args:
            term: The exact term key to remove.

        Returns:
            bool: True if the term existed and was removed (and the
            store was saved); False if no such term was present (in
            which case nothing is written).
        """
        if term in self.entries:
            del self.entries[term]
            self.save()
            return True
        else:
            return False

    def add(self, term: str, definition: str = "", category: str = DEFAULT_CATEGORY, adoption: str = DEFAULT_ADOPTION, team: str = DEFAULT_TEAM) -> None:
        """Add or overwrite an entry and persist the change.

        All string arguments are stripped; empty ``category``,
        ``adoption``, or ``team`` values fall back to their defaults.
        If ``term`` already exists, it is silently overwritten (callers
        that want duplicate detection should check via
        :meth:`get_entry` first).

        Args:
            term: The term to add. Required; whitespace-only is treated
                as empty.
            definition: The definition text. Defaults to empty (e.g. to
                be filled in later via :meth:`set_definition`).
            category: Grouping label. Defaults to :data:`DEFAULT_CATEGORY`.
            adoption: Adoption/status label. Defaults to
                :data:`DEFAULT_ADOPTION`.
            team: Associated :class:`Team` value. Defaults to
                :data:`DEFAULT_TEAM`.

        Raises:
            ValueError: If ``term`` is empty after stripping.
        """
        term, definition, category, adoption, team = (
            term.strip(),
            definition.strip(),
            (category.strip() or DEFAULT_CATEGORY),
            (adoption.strip() or DEFAULT_ADOPTION),
            (team.strip() or DEFAULT_TEAM),
        )
        if not term:
            raise ValueError("Term is required.")
        self.entries[term] = Entry(term=term, category=category, adoption=adoption, team=team, definition=definition)
        self.save()

    def set_definition(self, term: str, definition: str) -> bool:
        """Updates only the definition of an existing entry (e.g. once an
        async definition lookup resolves) and persists the change. Returns
        False if the term is no longer present (e.g. it was removed while
        the lookup was in flight)."""
        entry = self.entries.get(term)
        if entry is None:
            return False
        entry.definition = (definition or "").strip()
        self.save()
        return True

    def entries_missing_fields(self) -> list[Entry]:
        """Entries whose definition and/or category is empty. Used on
        startup to offer automatic sanitization via the AI agent."""
        return [
            e for e in self.entries.values()
            if not (e.definition or "").strip() or not (e.category or "").strip()
        ]

    def export_to(self, filepath: str) -> None:
        """Write all entries, sorted by term, to an arbitrary file path.

        Unlike :meth:`save`, this does not touch ``self.path`` and does
        not write the leading column-header comment, since callers
        typically use this for one-off exports/downloads.

        Args:
            filepath: Destination path to write to (overwritten if it
                already exists).
        """
        with open(filepath, "w", encoding="utf-8") as f:
            for term in sorted(self.entries, key=str.lower):
                e = self.entries[term]
                self.write_entry(f, e)

    def import_from(self, filepath: str, options: ImportOptions) -> tuple[int, int]:
        """Merge entries from another vocabulary-format file into this store.

        Lines are parsed the same way as :meth:`load` (same comment/blank
        handling and field-count rules via :meth:`_parse_line`). After
        merging, the resulting store is persisted via :meth:`save`.

        Args:
            filepath: Path to the file to import from.
            options: Controls whether the store is cleared first
                (``clear_before``) and how duplicate terms are handled
                (``preserve_existing``).

        Returns:
            tuple[int, int]: ``(num_added, num_skipped)`` — the number of
            entries that were newly added or overwritten, and the number
            of duplicate entries left untouched because
            ``options.preserve_existing`` was set.
        """
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
        """Find entries whose term matches a search query.

        Matching is case-insensitive. An empty query returns every entry.
        Otherwise, results are ranked in two tiers — terms that *start
        with* the query first, then terms that merely *contain* it — with
        each tier sorted case-insensitively by term.

        Args:
            query: The search string. Pass an empty string to list all
                entries.

        Returns:
            list[Entry]: Matching entries ordered as described above.
        """
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
    

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Options:
    max_pages: int = 5
    max_requests: int = 150
    max_seconds: int = 180
    image_kb: int = 500
    browser: bool = False
    browser_pages: int = 2
    spelling: bool = False
    language: str = "en"
    profile: str = "Business website"
    dictionary: list = field(default_factory=list)
    required_phrases: list = field(default_factory=list)
    forbidden_phrases: list = field(default_factory=list)
    journey: list = field(default_factory=list)
    form_checks: bool = False
    categories: list = field(default_factory=lambda: ["Links", "Images", "Metadata", "Accessibility", "Content", "Technical", "Brand", "Performance"])


@dataclass
class Issue:
    page: str
    category: str
    severity: str
    title: str
    evidence: str
    fix: str
    target: str = ""
    code: str = ""
    id: str = ""

    def __post_init__(self):
        if not self.id:
            raw = "|".join([self.page, self.code or self.title, self.target])
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:20]


@dataclass
class Scan:
    url: str
    options: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: str = field(default_factory=now)
    finished: str = ""
    pages: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    links: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    journey: list = field(default_factory=list)
    complete: bool = True

    def add(self, **kwargs):
        issue = asdict(Issue(**kwargs))
        if issue["id"] not in {i["id"] for i in self.issues}:
            self.issues.append(issue)

    def to_dict(self):
        return asdict(self)

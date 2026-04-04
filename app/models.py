from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Track:
    title: str
    artists: List[str]
    source: str
    duration_ms: Optional[int] = None
    spotify_uri: Optional[str] = None
    file_path: Optional[str] = None

    @property
    def normalized_title(self) -> str:
        from app.utils import normalize_text
        return normalize_text(self.title)

    @property
    def normalized_artists(self) -> List[str]:
        from app.utils import normalize_text
        return [normalize_text(artist) for artist in self.artists if normalize_text(artist)]

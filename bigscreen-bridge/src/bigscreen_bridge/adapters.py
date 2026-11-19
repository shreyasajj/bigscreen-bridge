"""Fixed app identities; playback hooks are negotiated separately from UI focus."""

from dataclasses import dataclass


@dataclass(frozen=True)
class App:
    id: str
    name: str
    flatpak: str
    aliases: tuple[str, ...]

    def matches(self, window: dict) -> bool:
        identities = {
            str(window.get(k, "")).lower().removesuffix(".desktop") for k in ("desktop", "resource", "class")
        }
        return bool(identities & {v.lower().removesuffix(".desktop") for v in (self.flatpak, *self.aliases)})


APPS = (
    App("couchside", "Couchside", "io.github.shreyasajj.Couchside", ("couchside",)),
    App("stremio", "Stremio TV", "io.github.shreyasajj.StremioTV", ("stremio-tv",)),
    App("flimmer", "Flimmer", "io.github.jclsn.Flimmer", ("flimmer",)),
    App("vacuumtube", "VacuumTube", "rocks.shy.VacuumTube", ("vacuumtube",)),
)


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

#!/usr/bin/env python3
"""Build custom_components/aureon_music from the upstream SpotifyPlus and Spotcast sources.

Both upstream projects are copied unchanged in behaviour; only what is needed to live inside one AUREON
integration is changed, by text rules listed below (so an upstream update can be re-applied the same way):
  * SpotifyPlus  -> aureon_music/spotifyplus_core  (MIT, Todd Lucas)
  * Spotcast     -> aureon_music/spotcast_core     (Apache-2.0, Mincka / fondberg / fcusson)
  * domain spotifyplus/spotcast -> aureon_music; absolute imports point to the new sub-packages;
    each keeps its own hass.data key; their config flows are no longer registered on their own
    (the AUREON flow lets the owner pick "Spotify account" or "Spotcast").

AUREON fixes on top of the upstream code live as patches in scripts/patches/ and are applied last, in name order
(e.g. spotifyplus-token-rotation.patch: refreshed Spotify tokens are stored before the next refresh).

Usage: build_from_upstream.py <spotifyplus custom_components/spotifyplus> <spotcast custom_components/spotcast> <out dir>
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

DOMAIN = "aureon_music"
TEXT = {".py", ".yaml", ".json", ".md", ".txt"}


def rewrite(root: Path, rules):
    for f in root.rglob("*"):
        if f.is_file() and f.suffix in TEXT:
            s = f.read_text(encoding="utf-8")
            n = s
            for a, b in rules:
                n = re.sub(a, b, n) if isinstance(a, re.Pattern) else n.replace(a, b)
            if n != s:
                f.write_text(n, encoding="utf-8")


def main(plus_src: str, cast_src: str, out: str):
    out = Path(out)
    plus, cast = out / "spotifyplus_core", out / "spotcast_core"
    for d in (plus, cast):
        if d.exists():
            shutil.rmtree(d)
    shutil.copytree(plus_src, plus, ignore=shutil.ignore_patterns("__pycache__", "brand"))
    shutil.copytree(cast_src, cast, ignore=shutil.ignore_patterns("__pycache__", "brand"))

    # ---- SpotifyPlus: own names everywhere (intents, sentence files, token file), so it never collides with an
    # installed original. Upstream links stay as they are.
    keep = ["homeassistantcomponent_spotifyplus", "spotifyplus_card", "thlucas1.github.io/spotifyplus"]
    rewrite(plus, [(k, k.upper().replace("_", "⁣")) for k in keep] + [
        ("SPOTIFYPLUS", "AUREON_MUSIC"), ("SpotifyPlus", "AureonMusic"), ("spotifyplus", DOMAIN),
        (re.compile(r"hass\.data\[DOMAIN\]"), "hass.data[DATA_KEY]"),
        (re.compile(r"hass\.data\.setdefault\(DOMAIN"), "hass.data.setdefault(DATA_KEY"),
        (re.compile(r"class (\w+)\(config_entry_oauth2_flow\.AbstractOAuth2FlowHandler, domain=DOMAIN\)"), r"class \1(config_entry_oauth2_flow.AbstractOAuth2FlowHandler)"),
    ] + [(k.upper().replace("_", "⁣"), k) for k in keep] + [
        # imports of the original package (renamed above) point to the sub-package
        ("custom_components.%s." % DOMAIN, "custom_components.%s.spotifyplus_core." % DOMAIN),
    ])
    for f in sorted(plus.rglob("*"), key=lambda p: -len(p.parts)):
        if "spotifyplus" in f.name:
            f.rename(f.with_name(f.name.replace("spotifyplus", DOMAIN)))

    # ---- Spotcast: only the domain constant, its imports and its data key.
    rewrite(cast, [
        ("custom_components.spotcast", f"custom_components.{DOMAIN}.spotcast_core"),
        ('DOMAIN = "spotcast"', f'DOMAIN = "{DOMAIN}"'),
        (re.compile(r"hass\.data\[DOMAIN\]"), "hass.data[DATA_KEY]"),
        (re.compile(r"hass\.data\.setdefault\(\s*DOMAIN"), "hass.data.setdefault(DATA_KEY"),
        (re.compile(r"hass\.data\.get\(DOMAIN"), "hass.data.get(DATA_KEY"),
        (re.compile(r",\s*domain=DOMAIN,"), ","),
        ("integration: spotcast", f"integration: {DOMAIN}"),
        # Names the owner sees on devices and entities: Spotify, never the upstream project name.
        ('return f"Spotcast ({', 'return f"Spotify ({'),
        ('return f"Spotcast - {', 'return f"Spotify - {'),
        ('name=f"Spotcast {', 'name=f"Spotify {'),
    ])
    # DATA_KEY next to DOMAIN in each const module, and imported wherever DATA_KEY is used.
    for core, key in ((plus, "aureon_music_spotifyplus"), (cast, "aureon_music_spotcast")):
        const = core / "const.py"
        c = const.read_text(encoding="utf-8")
        c = re.sub(r'^(DOMAIN\s*(?::\s*str)?\s*=\s*"aureon_music".*)$', r'\1\nDATA_KEY = "' + key + '"', c, count=1, flags=re.M)
        assert "DATA_KEY" in c, const
        const.write_text(c, encoding="utf-8")
        for f in core.rglob("*.py"):
            s = f.read_text(encoding="utf-8")
            if "DATA_KEY" in s and f != const and "import DATA_KEY" not in s and not re.search(r"DATA_KEY[,\s)]", s.split("hass.data")[0]):
                rel = "." * (len(f.relative_to(core).parts)) + "const"
                f.write_text(_insert_import(s, f"from {rel} import DATA_KEY  # AUREON: own hass.data key\n"), encoding="utf-8")

    merge_resources(out, plus, cast)
    m1 = json.loads((Path(plus_src) / "manifest.json").read_text())
    m2 = json.loads((Path(cast_src) / "manifest.json").read_text())
    (out / "UPSTREAM.json").write_text(json.dumps({"spotifyplus": m1["version"], "spotcast": m2["version"]}, indent=2) + "\n")
    apply_patches(out)


def apply_patches(out: Path):
    """AUREON fixes (scripts/patches/*.patch, paths relative to the integration folder). A patch that no longer
    applies stops the build, so a fix is never lost silently."""
    for patch in sorted((Path(__file__).parent / "patches").glob("*.patch")):
        subprocess.run(["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "-i", str(patch)], cwd=out, check=True)


def deep_merge(a, b):
    """b wins on leaf collisions; nested dicts are merged."""
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


MENU = {
    "en": {"title": "AUREON Music", "description": "How should AUREON Music talk to Spotify?", "menu_options": {"spotifyplus": "Spotify account: player, library and search (Spotify Web API)", "spotcast": "Play on Cast / Alexa speakers (Spotify Connect)"}},
    "de": {"title": "AUREON Musik", "description": "Wie soll AUREON Musik mit Spotify sprechen?", "menu_options": {"spotifyplus": "Spotify-Konto: Player, Mediathek und Suche (Spotify Web API)", "spotcast": "Auf Cast- / Alexa-Lautsprechern abspielen (Spotify Connect)"}},
    "pt": {"title": "AUREON Música", "description": "Como o AUREON Música deve falar com o Spotify?", "menu_options": {"spotifyplus": "Conta do Spotify: player, biblioteca e busca (Spotify Web API)", "spotcast": "Tocar em caixas Cast / Alexa (Spotify Connect)"}},
    "fr": {"title": "AUREON Musique", "description": "Comment AUREON Musique doit-il parler à Spotify ?", "menu_options": {"spotifyplus": "Compte Spotify : lecteur, bibliothèque et recherche (Spotify Web API)", "spotcast": "Lire sur les enceintes Cast / Alexa (Spotify Connect)"}},
}
# The owner sees one product: user-facing texts never name the upstream projects (credits stay in NOTICE.md).
PRODUCT = {"en": "AUREON Music", "de": "AUREON Musik", "pt": "AUREON Música", "fr": "AUREON Musique"}


def neutral_names(value, lang="en"):
    """Replace the upstream project names in every user-facing text value (keys stay untouched)."""
    if isinstance(value, dict):
        return {k: neutral_names(v, lang) for k, v in value.items()}
    if isinstance(value, list):
        return [neutral_names(v, lang) for v in value]
    if isinstance(value, str):
        name = PRODUCT.get(lang, PRODUCT["en"])
        for word in ("SpotifyPlus", "Spotcast"):
            value = value.replace(f"{word} integration", f"{name} integration").replace(f"Die {word} Integration", f"Die {name} Integration").replace(word, name)
        return value
    return value


def preserve_native_resources(merged: dict, previous: dict) -> None:
    """Keep first-party OAuth labels when regenerating vendored translations."""
    current = merged.setdefault("config", {})
    old = previous.get("config", {})
    old_steps = old.get("step", {})
    steps = current.setdefault("step", {})
    for key in ("native", "native_reauth"):
        if key in old_steps:
            steps[key] = old_steps[key]
    native = old_steps.get("user", {}).get("menu_options", {}).get("native")
    if native:
        # MENU is shared across strings and translations.
        steps["user"] = {**steps["user"], "menu_options": {**steps["user"]["menu_options"], "native": native}}
    if "invalid_client_id" in old.get("error", {}):
        current.setdefault("error", {})["invalid_client_id"] = old["error"]["invalid_client_id"]
    old_abort = old.get("abort", {})
    for key in ("wrong_account", "native_callback_unavailable", "native_storage_error", "native_oauth_failed"):
        if key in old_abort:
            current.setdefault("abort", {})[key] = old_abort[key]


def merge_resources(out: Path, plus: Path, cast: Path):
    (out / "services.yaml").write_text("# SpotifyPlus part\n" + (plus / "services.yaml").read_text(encoding="utf-8") + "\n\n# Spotcast part\n" + (cast / "services.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    load = lambda p: json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    strings = deep_merge(load(plus / "strings.json"), load(cast / "strings.json"))
    strings.setdefault("config", {}).setdefault("step", {})["user"] = MENU["en"]
    preserve_native_resources(strings, load(out / "strings.json"))
    strings = neutral_names(strings, "en")
    (out / "strings.json").write_text(json.dumps(strings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tr = out / "translations"
    tr.mkdir(exist_ok=True)
    for lang in ("en", "de", "pt", "fr"):
        data = deep_merge(load(plus / "translations" / f"{lang}.json"), load(cast / "translations" / f"{lang}.json"))
        if lang in MENU:
            data.setdefault("config", {}).setdefault("step", {})["user"] = MENU[lang]
        preserve_native_resources(data, load(tr / f"{lang}.json"))
        data = neutral_names(data, lang)
        if data:
            (tr / f"{lang}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if (cast / "icons.json").exists():
        shutil.copy(cast / "icons.json", out / "icons.json")


def _insert_import(s: str, line: str) -> str:
    """Insert after the module docstring and any `from __future__` imports."""
    import ast
    tree = ast.parse(s)
    after = 0
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str) and after == 0) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            after = node.end_lineno
        else:
            break
    lines = s.splitlines(keepends=True)
    return "".join(lines[:after]) + line + "".join(lines[after:])


if __name__ == "__main__":
    main(*sys.argv[1:4])

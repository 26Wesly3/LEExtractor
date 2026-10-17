"""把本地 XPI 注册进 Zotero 的 extensions.json（等价于"从文件安装"的结果）。"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

profile = Path(os.environ["APPDATA"]) / "Zotero" / "Zotero" / "Profiles" / "1n801fbr.default"
path = profile / "extensions.json"
xpi = Path(r"E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work\zotero-plugin\dist\LEExtractor.xpi")
uri = xpi.as_uri()

doc = json.loads(path.read_text(encoding="utf-8"))
doc["addons"] = [a for a in doc.get("addons", []) if a.get("id") != "leextractor@local"]
now = int(time.time() * 1000)
doc["addons"].append(
    {
        "id": "leextractor@local",
        "syncGUID": "{%s}" % uuid.uuid4(),
        "version": "0.2.0",
        "type": "extension",
        "loader": None,
        "updateURL": "http://127.0.0.1:8000/plugin/update.json",
        "installOrigins": None,
        "manifestVersion": 2,
        "optionsURL": None,
        "optionsType": None,
        "optionsBrowserStyle": True,
        "aboutURL": None,
        "defaultLocale": {
            "name": "LEExtractor",
            "description": "在 Zotero 条目面板里连接本机 LEExtractor",
            "creator": "LEExtractor",
            "homepageURL": "http://127.0.0.1:8000/",
            "developers": None,
            "translators": None,
            "contributors": None,
        },
        "visible": True,
        "active": True,
        "userDisabled": False,
        "appDisabled": False,
        "embedderDisabled": False,
        "installDate": now,
        "updateDate": now,
        "applyBackgroundUpdates": 1,
        "path": str(xpi),
        "skinnable": False,
        "sourceURI": uri,
        "releaseNotesURI": None,
        "softDisabled": False,
        "foreignInstall": False,
        "strictCompatibility": True,
        "locales": [],
        "targetApplications": [
            {"id": "zotero@zotero.org", "minVersion": "7.9.9", "maxVersion": "10.9.9"}
        ],
        "targetPlatforms": [],
        "signedState": 0,
        "signedTypes": [],
        "signedDate": None,
        "seen": True,
        "dependencies": [],
        "incognito": "spanning",
        "location": "app-profile",
        "rootURI": uri,
        "enabledScopes": [],
        "versionScheme": None,
        "integrity": None,
    }
)
path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print("registered; addons =", [a["id"] for a in doc["addons"]])
print("rootURI =", uri)

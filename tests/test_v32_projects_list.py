"""S8：GET /api/v3/projects —— 项目列表。

只列当前会话 owner 的项目（不泄露别人的），并按最近更新排在前面。这是 v3.2
契约（25 个操作）之外的扩展，所以单独一个文件，不去改动冻结的契约用例。
"""
from __future__ import annotations

import time

from tests.test_v32_api import _csrf, _headers, _project, app, client  # noqa: F401  (fixtures)


def test_projects_lists_the_owners_projects_newest_first(client):
    csrf = _csrf(client)
    first = _project(client, csrf, name="alpha")
    time.sleep(0.01)
    second = _project(client, csrf, name="beta")

    response = client.get("/api/v3/projects")
    assert response.status_code == 200, response.text
    payload = response.json()
    ids = [row["project_id"] for row in payload["items"]]
    assert ids == [second["project_id"], first["project_id"]]
    assert payload["total"] == 2
    # DTO 与单个项目接口一致，且不含 owner_id。
    assert set(payload["items"][0]) == set(client.get(f"/api/v3/projects/{second['project_id']}").json())
    assert "owner_id" not in payload["items"][0]


def test_projects_needs_a_session_and_is_empty_for_a_fresh_one(client):
    # 与其它 v3 读接口一致：没有会话就是 401，不隐式创建。
    assert client.get("/api/v3/projects").status_code == 401
    _csrf(client)
    assert client.get("/api/v3/projects").json() == {"items": [], "total": 0}


def test_projects_never_lists_another_owners_project(client):
    csrf = _csrf(client)
    mine = _project(client, csrf, name="mine")

    client.cookies.clear()
    other = _project(client, _csrf(client), name="theirs")

    payload = client.get("/api/v3/projects").json()
    ids = [row["project_id"] for row in payload["items"]]
    assert mine["project_id"] not in ids
    assert other["project_id"] in ids

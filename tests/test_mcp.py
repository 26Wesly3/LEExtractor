import asyncio

from fastmcp import Client

from litsearch.demo import demo_state
from litsearch.persistence import state_to_dict
from litsearch.server import mcp


def test_mcp_tools_register_and_share_the_same_evidence_model():
    async def check():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert {"search_literature", "explain_paper", "build_research_landscape", "get_evidence_path", "export_research_bundle", "literature_review_search"} <= names
            session = state_to_dict(demo_state())
            result = await client.call_tool("build_research_landscape", {"session": session})
            assert not result.is_error
            assert result.data["summary"]["nodes"] == 8
            assert result.data["summary"]["edges"] == 6
            explained = await client.call_tool("explain_paper", {"session": session, "paper_id": "demo:1"})
            assert explained.data["discovery_traces"][0]["method"] == "demo_fixture"
    asyncio.run(check())

"""One-off: create the PRSolution Team + BeOne channel and add members.

Members on infovision.com are guests relative to the agent's alphametricx.com
tenant, so they're added via team_add_guest.
"""

import asyncio
import json
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from app.channels.teams_mcp import TeamsMcpAdapter  # noqa: E402
from app.config.settings import get_settings  # noqa: E402

MEMBERS = [
    "harish.thulasiram@infovision.com",
    "khadar.syed@infovision.com",
    "Anuran.Chakraborty@infovision.com",
]


def _text(res) -> str:
    out = ""
    for item in res.content:
        out += getattr(item, "text", "") or ""
    return out


async def main() -> None:
    a = TeamsMcpAdapter()
    async with streamablehttp_client(get_settings().teams_mcp_url, auth=a._auth_provider()) as (
        r, w, *_,
    ), ClientSession(r, w) as s:
        await s.initialize()

        # already exists?
        existing = _text(await s.call_tool("team_list", {}))
        print("existing teams:", existing[:400])

        team = _text(await s.call_tool("team_create", {
            "displayName": "PRSolution",
            "description": "PR Intelligence Agent — media monitoring workspace",
            "visibility": "private",
        }))
        print("\nteam_create ->", team[:500])
        team_id = None
        try:
            team_id = json.loads(team).get("id") or json.loads(team).get("teamId")
        except Exception:
            import re
            m = re.search(r'"id"\s*:\s*"([^"]+)"', team)
            team_id = m.group(1) if m else None
        print("team_id =", team_id)
        if not team_id:
            print("!! could not parse team id; stopping")
            return

        # provisioning is async — wait before channel creation
        await asyncio.sleep(20)

        ch = _text(await s.call_tool("channel_create", {
            "teamId": team_id, "displayName": "BeOne",
            "description": "BeOne brand monitoring", "membershipType": "standard",
        }))
        print("\nchannel_create ->", ch[:400])

        for email in MEMBERS:
            try:
                res = _text(await s.call_tool("team_add_guest", {
                    "teamId": team_id, "email": email, "role": "member",
                }))
                print(f"add_guest {email} ->", res[:200])
            except Exception as exc:
                print(f"add_guest {email} FAILED:", str(exc)[:200])

        # welcome post to BeOne
        try:
            chid = json.loads(ch).get("id")
        except Exception:
            import re
            m = re.search(r'"id"\s*:\s*"([^"]+)"', ch)
            chid = m.group(1) if m else None
        if chid:
            await s.call_tool("channel_message_send", {
                "teamId": team_id, "channelId": chid,
                "text": "PR Intelligence Agent is now monitoring this channel. "
                        "Mention @agent with a brand (BeOne, Trane, or Otsuka) to start.",
            })
            print("\nposted welcome to BeOne channel")
        print(f"\nRESULT team_id={team_id} channel_id={chid}")


if __name__ == "__main__":
    asyncio.run(main())

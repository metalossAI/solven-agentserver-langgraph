from dotenv import load_dotenv
import os
load_dotenv()

from composio import Composio
from composio_langchain import LangchainProvider

composio = Composio(
    api_key=os.getenv("COMPOSIO_API_KEY")
)

# Same filter but without user_id (for schemas)
raw_tools = composio.tools.get_raw_composio_tools(
    toolkits=["GMAIL"],
    limit=5
)

scoped_tools = composio.tools.get(
    user_id="5abf428a-36ee-46a0-8381-8ba222871bed",
    toolkits=["outlook"],
)

# Create a session for the user
session = composio.create(user_id="5abf428a-36ee-46a0-8381-8ba222871bed")

# Get tools through the session
session_tools = session.tools()
print("SESSION TOOLS:", session_tools)

# Check toolkits in the session
toolkits = session.toolkits()
print("SESSION TOOLKITS:", toolkits)

# Get detailed info about connections
accounts = composio.connected_accounts.list(user_ids=["5abf428a-36ee-46a0-8381-8ba222871bed"])
print("DETAILED ACCOUNTS:")
for account in accounts.items:
    print(f"  Toolkit: {account.toolkit.slug}")
    print(f"  Status: {account.status}")
    print(f"  ID: {account.id}")
    print("---")


# Test both toolkits with correct casing
toolkits_to_test = ["outlook", "gmail"]

for toolkit in toolkits_to_test:
    tools = composio.tools.get(
        user_id="5abf428a-36ee-46a0-8381-8ba222871bed",
        toolkits=[toolkit],
        limit=5
    )
    print(f"\n{toolkit.upper()} TOOLS:")
    print(f"  Count: {len(tools)}")
    if tools:
        print(tools)

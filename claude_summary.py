import os
import json
from dotenv import load_dotenv
import anthropic
from mcp_server import add_issue

load_dotenv()

client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1000,
    messages=[
        {
            "role": "user",
           "content": """
    Analyze this business issue:

    Marketing campaign conversion dropped significantly in Bengaluru.

    Return ONLY valid JSON.

    Use exactly this structure:

    {
        "section": "",
        "site": "",
        "issue": "",
        "count": 0,
        "days_open": 0,
        "action_taken": "",
        "next_action": "",
        "status": "",
        "notes": ""
    }

    Do not include Markdown.
    Do not include explanations outside the JSON.
    """
        }
    ]
)
# Get Claude's response
text = response.content[0].text

# Convert JSON text into a Python dictionary
data = json.loads(text)

# Add the Claude-generated issue to Google Sheets
result = add_issue(
    section=data["section"],
    site=data["site"],
    issue=data["issue"],
    count=data["count"],
    days_open=data["days_open"],
    action_taken=data["action_taken"],
    next_action=data["next_action"],
    status=data["status"],
    notes=data["notes"]
)

print(result)


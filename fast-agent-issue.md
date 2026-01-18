# Issue: Elicitation forms don't populate default values for string/number fields

## Description
The elicitation form implementation in `src/mcp_agent/human_input/elicitation_form.py` does not populate default values for string, integer, or number fields, even though the JSON schema includes these defaults. Only boolean fields currently show their default values.

## Current Behavior
When an MCP server sends an elicitation request with a schema containing default values:
```python
class CommitElicitationSchema(BaseModel):
    message: str = Field(
        default="Initial commit message",
        description="Commit message"
    )
```

The resulting form displays an empty text field instead of pre-populating it with "Initial commit message".

## Expected Behavior
Text input fields should be pre-populated with their default values from the schema, similar to how boolean fields already work:
- String fields should show the default text
- Integer/number fields should show the default number
- Users should be able to modify or clear the default value

## Root Cause
In `elicitation_form.py`, when creating a Buffer for text input (lines ~627-633), the Buffer is initialized without any initial document:

```python
buffer = Buffer(
    validator=validator,
    multiline=multiline,
    validate_while_typing=True,
    complete_while_typing=False,
    enable_history_search=False,
)
```

The default value from `field_def.get("default")` is never used, unlike boolean fields which correctly set `checkbox.checked = default`.

## Proposed Fix
Add default value support when creating the Buffer:

```python
# Get default value if present
default_text = field_def.get("default", "")

# Create document with default text if provided
from prompt_toolkit.document import Document
initial_document = Document(text=str(default_text)) if default_text else None

buffer = Buffer(
    document=initial_document,
    validator=validator,
    multiline=multiline,
    validate_while_typing=True,
    complete_while_typing=False,
    enable_history_search=False,
)
```

## Impact
This affects all MCP servers that use elicitation with default values for fields, preventing a smooth user experience where forms can be pre-populated with sensible defaults. This is particularly important for:
- Git commit messages with suggested text
- Form fields with common values
- Configuration settings with recommended defaults

## Steps to Reproduce
1. Create an MCP server that sends an elicitation with a string field containing a default value
2. Connect to the server using fast-agent
3. Observe that the text field appears empty instead of showing the default value

## Additional Context
- The `form_fields.py` module already supports defaults in its schema definitions
- The JSON schema correctly includes the default values when sent to the client
- This has never worked (checked git history back to initial implementation)
- Only boolean fields currently show their defaults correctly
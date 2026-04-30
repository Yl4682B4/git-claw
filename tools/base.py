class Tool:
    """A tool that can be called by the LLM."""

    def __init__(self, name, description, parameters, impl):
        self.name = name
        self.description = description
        self.parameters = parameters  # OpenAI JSON Schema format
        self.impl = impl

    def to_openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }

    def execute(self, **kwargs):
        return self.impl(**kwargs)

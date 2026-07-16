def run(xml: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "merchants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "rating": {"type": "string"},
                        "distance": {"type": "string"}
                    },
                    "required": ["name", "rating", "distance"]
                }
            }
        },
        "required": ["merchants"]
    }

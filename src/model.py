from langchain_openai import ChatOpenAI

config = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-7f8b123be5de4db496fcc2d72d9fce87",
    "model": "deepseek-v4-flash"
}



def build_model():
    return  ChatOpenAI(
        base_url = config["base_url"], 
        api_key=config["api_key"],
        model=config["model"],
        streaming=False
    )
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.config import create_llm, load_env
from agent.core import AgentLoop

load_env()
llm = create_llm()

app = FastAPI()

# Enable CORS for Flutter Web running on localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    loop = AgentLoop(
        mock_base_url="http://127.0.0.1:8001",
        role="teacher",
        user="teacher.ahmed@school-a.edu",
        school="school-a",
        llm=llm,
    )
    try:
        result = loop.run(req.message)
        return {"reply": result.answer}
    finally:
        loop.close()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
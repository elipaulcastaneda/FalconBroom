from fastapi import FastAPI
app = FastAPI()

@app.get("/me")
def me():
    return {"ok": True}

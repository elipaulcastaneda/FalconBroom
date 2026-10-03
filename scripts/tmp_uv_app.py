from fastapi import FastAPI
app = FastAPI()

@app.get('/health')
def health():
    return {'ok': True}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=3009, log_level='debug')

from pydantic import BaseModel


class Chunk(BaseModel):
    text: str
    source: str
    chunk_index: int


class IndexRequest(BaseModel):
    key: str
    chunks: list[Chunk]


class SearchRequest(BaseModel):
    key: str
    query: str
    limit: int = 5
    synthesize: bool = False


class ClearRequest(BaseModel):
    key: str

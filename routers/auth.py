from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Response
from fastapi.responses import JSONResponse

from db import client as db
from core.config import AUTH_COOKIE_NAME
from core.schemas import LoginRequest

router = APIRouter()

@router.post("/api/auth/login")
async def login(request: LoginRequest):
    user = db.verify_login(request.login, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = db.create_session_token(user["id"])
    return JSONResponse(
        {"success": True, "login": user["login"], "role": user["role"]},
        headers={
            "Set-Cookie": f"{AUTH_COOKIE_NAME}={token}; Path=/; SameSite=lax; HttpOnly; Max-Age={60*60*24*30}"
        }
    )

@router.post("/api/auth/logout")
async def logout(response: Response, qa_session_token: Optional[str] = Cookie(default=None)):
    if qa_session_token:
        db.delete_token(qa_session_token)
    response.delete_cookie(AUTH_COOKIE_NAME)
    return JSONResponse({"success": True})

@router.get("/api/auth/me")
async def auth_me(qa_session_token: Optional[str] = Cookie(default=None)):
    user = db.get_user_by_token(qa_session_token) if qa_session_token else None
    if not user:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "login": user["login"], "role": user["role"]})

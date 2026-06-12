from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(pattern="^(admin|moderator)$")


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    role: Optional[str] = Field(default=None, pattern="^(admin|moderator)$")
    is_active: Optional[bool] = None
    theme: Optional[str] = Field(default=None, pattern="^(dark|light)$")


class UserRead(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str
    is_active: bool
    theme: str
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)

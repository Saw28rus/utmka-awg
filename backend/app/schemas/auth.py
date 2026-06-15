from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class CurrentUser(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str
    is_active: bool
    theme: str = "dark"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

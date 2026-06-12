#!/usr/bin/env python3
"""Emergency admin password reset on VPS.

Usage:
  docker compose exec backend python /host/utmka-awg/scripts/reset-admin.py NEW_PASSWORD
"""

import asyncio
import os
import sys

from sqlalchemy import select

sys.path.insert(0, "/app")

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User


async def main() -> None:
    if len(sys.argv) < 2:
        print("Укажи новый пароль: reset-admin.py NEW_PASSWORD")
        sys.exit(1)
    password = sys.argv[1]
    email = os.getenv("ADMIN_EMAIL", "admin@utmka.app").lower()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.role == "admin"))
        admins = list(result.scalars().all())
        if not admins:
            admin = User(
                email=email,
                password_hash=hash_password(password),
                name="Администратор",
                role="admin",
                is_active=True,
            )
            session.add(admin)
        else:
            admins[0].password_hash = hash_password(password)
            admins[0].is_active = True
        await session.commit()
    print(f"Пароль администратора ({email}) обновлён.")


if __name__ == "__main__":
    asyncio.run(main())

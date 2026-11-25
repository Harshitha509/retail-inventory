"""Create missing role-demo accounts; existing accounts are never modified.

Run from backend with: python -m app.provision_demo_users
Generated passwords are emitted once on successful provisioning only.
"""
import secrets

from sqlalchemy import select, func

from .auth import password_hash
from .db import SessionLocal
from .models import User

DEMO_USERS = (
    ("engineer.demo@retail.local", "Demo · Data Engineer", "DATA_ENGINEER"),
    ("inventory.demo@retail.local", "Demo · Inventory Manager", "INVENTORY_MANAGER"),
    ("viewer.demo@retail.local", "Demo · Viewer", "VIEWER"),
)


def provision(session_factory=SessionLocal, token_factory=secrets.token_urlsafe, hasher=password_hash):
    credentials = []
    with session_factory.begin() as db:
        if not db.scalar(select(func.count()).select_from(User).where(User.role == "ADMIN", User.is_active.is_(True))):
            raise RuntimeError("An active administrator must exist before provisioning demonstration accounts.")
        for email, name, role in DEMO_USERS:
            if db.scalar(select(User).where(User.email == email)) is not None:
                continue
            password = token_factory(32)
            db.add(User(email=email, full_name=name, password_hash=hasher(password), role=role,
                        is_active=True, must_change_password=True))
            credentials.append({"email": email, "role": role, "password": password})
    return credentials


def main():
    created = provision()
    if not created:
        print("No accounts created; all demonstration email addresses already exist and were left unchanged.")
        return
    print("Save these initial passwords now. They are not stored or shown again by this command.")
    for account in created:
        print(f"{account['email']} ({account['role']}): {account['password']}")


if __name__ == "__main__":
    main()

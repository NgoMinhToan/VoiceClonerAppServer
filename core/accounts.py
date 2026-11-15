from typing import Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from VoiceClonerAppServer.models.db_models import AnonymousUser, User

Account = Union[User, AnonymousUser]

ACCOUNT_TYPE_USER = "user"
ACCOUNT_TYPE_ANONYMOUS = "anonymous"


def account_type_of(account: Account) -> str:
    """Return the JWT `acct` claim value for an account instance."""
    return ACCOUNT_TYPE_ANONYMOUS if isinstance(account, AnonymousUser) else ACCOUNT_TYPE_USER


async def get_account_by_id(
    db: AsyncSession, account_id: str, account_type: str
) -> Optional[Account]:
    """Load an account from the table selected by its type; unknown types resolve as users."""
    model = AnonymousUser if account_type == ACCOUNT_TYPE_ANONYMOUS else User
    result = await db.execute(select(model).where(model.id == account_id))
    return result.scalar_one_or_none()

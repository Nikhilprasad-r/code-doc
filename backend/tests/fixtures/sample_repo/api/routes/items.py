"""Item routes."""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import SessionDep, get_current_user
from api.models import Item, ItemCreate

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=list[Item])
def read_items(session: SessionDep, skip: int = 0, limit: int = 100) -> list[Item]:
    """Return a page of items."""
    return session.query(Item).offset(skip).limit(limit).all()


@router.post(
    "/",
    response_model=Item,
)
async def create_item(*, session: SessionDep, item_in: ItemCreate, user=Depends(get_current_user)) -> Item:
    item = Item.model_validate(item_in, update={"owner_id": user.id})
    session.add(item)
    session.commit()
    return item


@router.delete("/{item_id}")
def delete_item(session: SessionDep, item_id: int) -> None:
    item = session.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    session.delete(item)


class ItemService:
    """Business logic for items."""

    def __init__(self, session):
        self.session = session

    def archive(self, item_id: int) -> None:
        item = self.session.get(Item, item_id)
        item.archived = True
        self.session.commit()

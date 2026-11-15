from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..menu import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    delete_item,
    get_item,
    list_items,
    update_item,
)
from .deps import get_session
from .schemas import (
    MenuItemIn,
    MenuItemOut,
    MenuItemUpdate,
    ModifierGroupIn,
    ModifierGroupOut,
    ModifierIn,
    ModifierOut,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.post(
    "/locations/{location_id}/menu/items",
    response_model=MenuItemOut,
    status_code=status.HTTP_201_CREATED,
)
def create_menu_item(location_id: str, body: MenuItemIn, session: SessionDep) -> MenuItemOut:
    item = create_item(session, location_id=location_id, **body.model_dump())
    session.commit()
    return MenuItemOut.model_validate(item)


@router.get("/locations/{location_id}/menu/items", response_model=list[MenuItemOut])
def list_menu_items(
    location_id: str, session: SessionDep, include_deleted: bool = False
) -> list[MenuItemOut]:
    items = list_items(session, location_id, include_deleted=include_deleted)
    return [MenuItemOut.model_validate(item) for item in items]


@router.get("/menu/items/{item_id}", response_model=MenuItemOut)
def read_menu_item(item_id: str, session: SessionDep) -> MenuItemOut:
    try:
        return MenuItemOut.model_validate(get_item(session, item_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="menu item not found") from exc


@router.patch("/menu/items/{item_id}", response_model=MenuItemOut)
def patch_menu_item(item_id: str, body: MenuItemUpdate, session: SessionDep) -> MenuItemOut:
    try:
        item = update_item(session, item_id, body.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="menu item not found") from exc
    session.commit()
    return MenuItemOut.model_validate(item)


@router.delete("/menu/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_menu_item(item_id: str, session: SessionDep) -> None:
    try:
        delete_item(session, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="menu item not found") from exc
    session.commit()


@router.post(
    "/locations/{location_id}/menu/modifier-groups",
    response_model=ModifierGroupOut,
    status_code=status.HTTP_201_CREATED,
)
def create_group(location_id: str, body: ModifierGroupIn, session: SessionDep) -> ModifierGroupOut:
    group = create_modifier_group(session, location_id=location_id, **body.model_dump())
    session.commit()
    return ModifierGroupOut.model_validate(group)


@router.post(
    "/menu/modifier-groups/{group_id}/modifiers",
    response_model=ModifierOut,
    status_code=status.HTTP_201_CREATED,
)
def create_group_modifier(group_id: str, body: ModifierIn, session: SessionDep) -> ModifierOut:
    try:
        modifier = create_modifier(session, group_id=group_id, **body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="modifier group not found") from exc
    session.commit()
    return ModifierOut.model_validate(modifier)


@router.post(
    "/menu/items/{item_id}/modifier-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def attach_group(item_id: str, group_id: str, session: SessionDep) -> None:
    try:
        attach_modifier_group(session, menu_item_id=item_id, modifier_group_id=group_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()

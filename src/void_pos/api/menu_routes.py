"""Menu management. Tills read their own location's menu; only the admin edits it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..menu import (
    attach_modifier_group,
    bulk_update_items,
    create_item,
    create_modifier,
    create_modifier_group,
    delete_item,
    get_item,
    list_changes,
    list_items,
    revert_change,
    till_menu,
    update_item,
    update_modifier,
    update_modifier_group,
)
from .auth import AdminDep, PrincipalDep
from .deps import get_session
from .schemas import (
    BulkMenuUpdate,
    MenuChangeOut,
    MenuItemIn,
    MenuItemOut,
    MenuItemUpdate,
    ModifierGroupIn,
    ModifierGroupOut,
    ModifierGroupUpdate,
    ModifierIn,
    ModifierOut,
    ModifierUpdate,
    TillMenuOut,
)

router = APIRouter(tags=["menu"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/locations/{location_id}/menu", response_model=TillMenuOut)
def read_till_menu(location_id: str, session: SessionDep, principal: PrincipalDep) -> TillMenuOut:
    """The whole menu for a till: items, their modifier groups, and what is on sale now."""
    principal.require_location(location_id)
    return TillMenuOut.of(till_menu(session, location_id))


@router.post(
    "/locations/{location_id}/menu/items",
    response_model=MenuItemOut,
    status_code=status.HTTP_201_CREATED,
)
def create_menu_item(
    location_id: str, body: MenuItemIn, session: SessionDep, _: AdminDep
) -> MenuItemOut:
    item = create_item(session, location_id=location_id, **body.model_dump())
    session.commit()
    return MenuItemOut.model_validate(item)


@router.get("/locations/{location_id}/menu/items", response_model=list[MenuItemOut])
def list_menu_items(
    location_id: str, session: SessionDep, principal: PrincipalDep, include_deleted: bool = False
) -> list[MenuItemOut]:
    principal.require_location(location_id)
    items = list_items(session, location_id, include_deleted=include_deleted)
    return [MenuItemOut.model_validate(item) for item in items]


@router.patch("/locations/{location_id}/menu/items", response_model=list[MenuItemOut])
def bulk_edit_menu_items(
    location_id: str, body: BulkMenuUpdate, session: SessionDep, _: AdminDep
) -> list[MenuItemOut]:
    """Edit several items in one go (a price rise, switching a section off). All or none."""
    items = bulk_update_items(
        session,
        location_id,
        [(edit.id, edit.model_dump(exclude_unset=True, exclude={"id"})) for edit in body.updates],
    )
    session.commit()
    return [MenuItemOut.model_validate(item) for item in items]


@router.get("/menu/items/{item_id}", response_model=MenuItemOut)
def read_menu_item(item_id: str, session: SessionDep, principal: PrincipalDep) -> MenuItemOut:
    item = get_item(session, item_id)
    principal.require_location(item.location_id)
    return MenuItemOut.model_validate(item)


@router.patch("/menu/items/{item_id}", response_model=MenuItemOut)
def patch_menu_item(
    item_id: str, body: MenuItemUpdate, session: SessionDep, _: AdminDep
) -> MenuItemOut:
    item = update_item(session, item_id, body.model_dump(exclude_unset=True))
    session.commit()
    return MenuItemOut.model_validate(item)


@router.delete("/menu/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_menu_item(item_id: str, session: SessionDep, _: AdminDep) -> None:
    delete_item(session, item_id)
    session.commit()


@router.post(
    "/locations/{location_id}/menu/modifier-groups",
    response_model=ModifierGroupOut,
    status_code=status.HTTP_201_CREATED,
)
def create_group(
    location_id: str, body: ModifierGroupIn, session: SessionDep, _: AdminDep
) -> ModifierGroupOut:
    group = create_modifier_group(session, location_id=location_id, **body.model_dump())
    session.commit()
    return ModifierGroupOut.model_validate(group)


@router.patch("/menu/modifier-groups/{group_id}", response_model=ModifierGroupOut)
def patch_group(
    group_id: str, body: ModifierGroupUpdate, session: SessionDep, _: AdminDep
) -> ModifierGroupOut:
    group = update_modifier_group(session, group_id, body.model_dump(exclude_unset=True))
    session.commit()
    return ModifierGroupOut.model_validate(group)


@router.patch("/menu/modifiers/{modifier_id}", response_model=ModifierOut)
def patch_modifier(
    modifier_id: str, body: ModifierUpdate, session: SessionDep, _: AdminDep
) -> ModifierOut:
    modifier = update_modifier(session, modifier_id, body.model_dump(exclude_unset=True))
    session.commit()
    return ModifierOut.model_validate(modifier)


@router.post(
    "/menu/modifier-groups/{group_id}/modifiers",
    response_model=ModifierOut,
    status_code=status.HTTP_201_CREATED,
)
def create_group_modifier(
    group_id: str, body: ModifierIn, session: SessionDep, _: AdminDep
) -> ModifierOut:
    modifier = create_modifier(session, group_id=group_id, **body.model_dump())
    session.commit()
    return ModifierOut.model_validate(modifier)


@router.post(
    "/menu/items/{item_id}/modifier-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def attach_group(item_id: str, group_id: str, session: SessionDep, _: AdminDep) -> None:
    attach_modifier_group(session, menu_item_id=item_id, modifier_group_id=group_id)
    session.commit()


@router.get("/locations/{location_id}/menu/changes", response_model=list[MenuChangeOut])
def menu_changes(
    location_id: str,
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[MenuChangeOut]:
    """The menu's change history, newest first."""
    return [MenuChangeOut.of(c) for c in list_changes(session, location_id, limit=limit)]


@router.post("/menu/changes/{change_id}/revert", response_model=MenuChangeOut)
def revert_menu_change(change_id: str, session: SessionDep, _: AdminDep) -> MenuChangeOut:
    """Undo the latest change to an item, group or modifier; returns the undo's record."""
    change = revert_change(session, change_id)
    session.commit()
    return MenuChangeOut.of(change)

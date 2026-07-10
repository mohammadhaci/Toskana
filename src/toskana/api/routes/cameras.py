"""Camera CRUD (nested under restaurants) + pipeline control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import (
    ManagerDep,
    PageDep,
    SessionDep,
    camera_or_404,
    exit_group_or_404,
    model_or_404,
    restaurant_or_404,
)
from toskana.db.models import Camera
from toskana.vision.manager import CameraAlreadyRunning

router = APIRouter(tags=["cameras"])

_NESTED = "/restaurants/{restaurant_id}/cameras"


def _check_references(
    session: SessionDep, restaurant_id: int, exit_group_id: int | None, model_id: int | None
) -> None:
    """Reject cross-tenant / dangling references in a camera body (404)."""
    if exit_group_id is not None:
        exit_group_or_404(session, exit_group_id, restaurant_id)
    if model_id is not None:
        model_or_404(session, model_id, restaurant_id)


@router.get(_NESTED, response_model=schemas.Page[schemas.CameraRead])
def list_cameras(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(Camera).where(Camera.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(Camera.id).limit(page.limit).offset(page.offset)).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post(_NESTED, response_model=schemas.CameraRead, status_code=201)
def create_camera(restaurant_id: int, body: schemas.CameraCreate, session: SessionDep) -> Camera:
    restaurant_or_404(session, restaurant_id)
    _check_references(session, restaurant_id, body.exit_group_id, body.model_id)
    camera = Camera(restaurant_id=restaurant_id, **body.model_dump())
    session.add(camera)
    session.commit()
    return camera


@router.get(_NESTED + "/{camera_id}", response_model=schemas.CameraRead)
def get_camera(restaurant_id: int, camera_id: int, session: SessionDep) -> Camera:
    restaurant_or_404(session, restaurant_id)
    return camera_or_404(session, camera_id, restaurant_id)


@router.put(_NESTED + "/{camera_id}", response_model=schemas.CameraRead)
def update_camera(
    restaurant_id: int,
    camera_id: int,
    body: schemas.CameraUpdate,
    session: SessionDep,
    manager: ManagerDep,
) -> Camera:
    restaurant_or_404(session, restaurant_id)
    camera = camera_or_404(session, camera_id, restaurant_id)
    fields = body.model_dump(exclude_unset=True)
    _check_references(session, restaurant_id, fields.get("exit_group_id"), fields.get("model_id"))
    if fields.get("source_type", camera.source_type) == "usb":
        source_url = str(fields.get("source_url", camera.source_url))
        if not source_url.strip().isdigit():
            raise HTTPException(
                status_code=422, detail="usb source_url must be a numeric device index"
            )
    for key, value in fields.items():
        setattr(camera, key, value)
    session.commit()
    manager.reload_camera(camera_id)  # hot config reload if running
    return camera


@router.delete(_NESTED + "/{camera_id}", status_code=204)
def delete_camera(
    restaurant_id: int, camera_id: int, session: SessionDep, manager: ManagerDep
) -> None:
    restaurant_or_404(session, restaurant_id)
    camera = camera_or_404(session, camera_id, restaurant_id)
    manager.stop_camera(camera_id)
    session.delete(camera)
    session.commit()


# -- pipeline control ---------------------------------------------------------


@router.post("/cameras/{camera_id}/start", response_model=schemas.CameraStatus)
def start_camera(camera_id: int, session: SessionDep, manager: ManagerDep) -> schemas.CameraStatus:
    camera_or_404(session, camera_id)
    try:
        manager.start_camera(camera_id)
    except CameraAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _status_response(camera_id, manager)


@router.post("/cameras/{camera_id}/stop", response_model=schemas.CameraStatus)
def stop_camera(camera_id: int, session: SessionDep, manager: ManagerDep) -> schemas.CameraStatus:
    camera_or_404(session, camera_id)
    manager.stop_camera(camera_id)
    return _status_response(camera_id, manager)


@router.post("/cameras/{camera_id}/restart", response_model=schemas.CameraStatus)
def restart_camera(
    camera_id: int, session: SessionDep, manager: ManagerDep
) -> schemas.CameraStatus:
    camera_or_404(session, camera_id)
    manager.restart_camera(camera_id)
    return _status_response(camera_id, manager)


@router.get("/cameras/{camera_id}/status", response_model=schemas.CameraStatus)
def camera_status(camera_id: int, session: SessionDep, manager: ManagerDep) -> schemas.CameraStatus:
    camera_or_404(session, camera_id)
    return _status_response(camera_id, manager)


@router.post("/cameras/{camera_id}/calibrate", response_model=schemas.CameraStatus)
def calibrate_camera(
    camera_id: int, session: SessionDep, manager: ManagerDep
) -> schemas.CameraStatus:
    """Re-capture the drift-detection reference from the camera's current frame."""
    camera_or_404(session, camera_id)
    status = manager.calibrate_camera(camera_id)
    if status is None:
        raise HTTPException(
            status_code=409,
            detail="camera has no live frame to calibrate from (pipeline not running yet)",
        )
    return schemas.CameraStatus(**status)


def _status_response(camera_id: int, manager: ManagerDep) -> schemas.CameraStatus:
    status = manager.camera_status(camera_id)
    if status is None:
        return schemas.CameraStatus(camera_id=camera_id, running=False)
    return schemas.CameraStatus(**status)

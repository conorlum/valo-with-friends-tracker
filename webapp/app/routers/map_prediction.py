from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.friends import list_friends
from app.services.map_prediction import predict_map_probabilities
from app.templates import templates

router = APIRouter(prefix="/map-prediction", tags=["map-prediction"])

MAX_TEAMMATES = 4


@router.get("")
def map_prediction_page(
    request: Request, teammate: list[str] = Query(default=[]), db: Session = Depends(get_db)
):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    friends = list_friends(db, current_player.id)
    friend_by_name = {f.display_name: f for f in friends}
    selected = [friend_by_name[name] for name in teammate if name in friend_by_name][:MAX_TEAMMATES]

    predictions = None
    if selected:
        known_ids = [current_player.id] + [f.id for f in selected]
        predictions = predict_map_probabilities(db, known_ids)

    return templates.TemplateResponse(
        request,
        "map_prediction/index.html",
        {
            "friends": friends,
            "selected_names": {f.display_name for f in selected},
            "predictions": predictions,
        },
    )

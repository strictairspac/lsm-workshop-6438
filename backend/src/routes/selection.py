import json
from fastapi import APIRouter,Depends,Response
from ..config.database import get_db
from ..schemas.selection import SelectionInput
from ..services.auth import current_user,writer
from ..services.selection import listing,create,detail,resume
router=APIRouter(prefix='/selections')
@router.get('')
def get(db=Depends(get_db),user=Depends(current_user)):return listing(db)
@router.post('',status_code=202)
def add(data:SelectionInput,db=Depends(get_db),user=Depends(writer)):return create(db,data)
@router.get('/{ident}')
def one(ident:int,db=Depends(get_db),user=Depends(current_user)):return detail(db,ident)
@router.post('/{ident}/resume',status_code=202)
def resume_one(ident:int,db=Depends(get_db),user=Depends(writer)):return resume(db,ident)
@router.get('/{ident}/export')
def export(ident:int,db=Depends(get_db),user=Depends(current_user)):
    return Response(json.dumps(detail(db,ident),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="lsm-selection-{ident}.json"'})

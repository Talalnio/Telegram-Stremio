from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from Backend.fastapi.security.credentials import require_auth, verify_credentials
from Backend.fastapi.security.two_factor import admin_security, check_csrf, csrf_token
from Backend.helper.settings_manager import SettingsManager

router = APIRouter(prefix="/api/admin/security", dependencies=[Depends(require_auth)])


def private_response(data):
    return JSONResponse(data, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/2fa")
async def status(request: Request):
    state = await admin_security.state()
    return private_response({"enabled": state["enabled"], "remaining_codes": len(state.get("recovery_hashes", [])),
                             "csrf": csrf_token(request)})


@router.post("/2fa/{action}")
async def change(action: str, request: Request, payload: dict):
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    if action not in ("setup", "enable", "disable", "cancel"):
        raise HTTPException(404, "عملية غير معروفة.")
    await admin_security.throttle(request, "manage-factor")
    password = str(payload.get("password") or "")
    if not verify_credentials(SettingsManager.current().admin_username, password):
        raise HTTPException(400, "كلمة المرور غير صحيحة.")
    code = str(payload.get("code") or "").strip()
    if len(code) > 100:
        raise HTTPException(400, "الرمز غير صالح.")
    if action == "setup":
        result = await admin_security.setup(request)
    elif action == "enable":
        result = await admin_security.enable(request, code)
    elif action == "disable":
        result = await admin_security.disable(request, code)
    else:
        await admin_security.collection("admin_security").update_one(
            {"_id": "admin", "enabled": False}, {"$unset": {"pending": ""}}
        )
        result = {"cancelled": True}
    result["csrf"] = csrf_token(request)
    return private_response(result)

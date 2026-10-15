import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException

from VoiceClonerAppServer.models.schemas import VoiceProfileModel
from VoiceClonerAppServer.services.voice_service import voice_service

logger = logging.getLogger("VoiceClonerAppServer.VoicesAPI")

router = APIRouter(prefix="/v1/voices", tags=["Voices"])


@router.get("", response_model=List[VoiceProfileModel])
async def list_voices():
    """List all available voice profiles."""
    return voice_service.list_builtin_voices()


@router.get("/{voice_id}", response_model=VoiceProfileModel)
async def get_voice(voice_id: str):
    """Retrieve details for a specific voice profile."""
    voice = voice_service.get_voice_by_id(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    return voice

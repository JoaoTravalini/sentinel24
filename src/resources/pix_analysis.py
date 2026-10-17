from fastapi import status
from fastapi.responses import JSONResponse

from controllers.pix_analysis_controller import PixAnalysisController
from utils.schema_handler import SchemaHandler


class PixAnalysisResource:
    @SchemaHandler.validate("post_pix_transaction.json")
    def on_post(self, payload: dict) -> JSONResponse:
        body, created = PixAnalysisController().create(payload)

        response_status = (
            status.HTTP_201_CREATED
            if created
            else status.HTTP_200_OK
        )

        return JSONResponse(
            content=body,
            status_code=response_status,
        )

    def on_get(self, pix_id: str) -> JSONResponse:
        body = PixAnalysisController().get(pix_id)

        return JSONResponse(
            content=body,
            status_code=status.HTTP_200_OK,
        )
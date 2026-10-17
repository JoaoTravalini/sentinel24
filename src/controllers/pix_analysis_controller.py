from typing import NoReturn

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabasePoolTimeout

from controllers.base_controller import BaseController
from dtos.pix_analysis_dto import PixAnalysisDTO
from errors import InvalidParameter
from errors.pix_analysis_errors import (
    PixAnalysisNotFound,
    PixAnalysisUnavailable,
    PixIdConflict,
)
from repositories.pix_analysis_repository import PixAnalysisRepository
from rules.engine import RiskEngine
from utils.pix_payload import calculate_payload_hash, prepare_pix_payload


class PixAnalysisController(BaseController):
    def __init__(self) -> None:
        super().__init__(__name__)
        self.repository = PixAnalysisRepository(self.context)
        self.engine = RiskEngine()

    def _existing_response(
        self,
        analysis,
        payload_hash: str,
    ) -> tuple[dict, bool]:
        if analysis.payload_hash != payload_hash:
            raise PixIdConflict()

        body = PixAnalysisDTO.obj_to_dict(analysis)
        self.session.rollback()

        return body, False

    def _handle_database_error(self, error) -> NoReturn:
        self.session.rollback()
        code = getattr(getattr(error, "orig", None), "pgcode", None)

        if (
            isinstance(error, (OperationalError, DatabasePoolTimeout))
            or getattr(error, "connection_invalidated", False)
            or code in {"57014", "55P03", "40001", "40P01"}
        ):
            self.logger.warning(
                "Pix analysis interrupted by database unavailability "
                "or contention."
            )
            raise PixAnalysisUnavailable() from error

        raise error

    def create(self, payload: dict) -> tuple[dict, bool]:
        normalized = prepare_pix_payload(payload)
        payload_hash = calculate_payload_hash(normalized)

        try:
            self.repository.configure_transaction()

            existing = self.repository.get_by_pix_id(normalized["id"])
            if existing is not None:
                return self._existing_response(existing, payload_hash)

            self.repository.lock_analysis_inputs(normalized)

            existing = self.repository.get_by_pix_id(normalized["id"])
            if existing is not None:
                return self._existing_response(existing, payload_hash)

            received_at = self.repository.database_time()
            decision = self.engine.evaluate(normalized)

            analysis = self.repository.create(
                raw_payload=payload,
                normalized=normalized,
                payload_hash=payload_hash,
                decision=decision,
                received_at=received_at,
            )

            body = PixAnalysisDTO.obj_to_dict(analysis)
            self.session.commit()

            return body, True

        except (DBAPIError, DatabasePoolTimeout) as error:
            self._handle_database_error(error)
        except Exception:
            self.session.rollback()
            raise

    def get(self, pix_id: str) -> dict:
        if not pix_id.strip() or len(pix_id) > 128 or "\x00" in pix_id:
            raise InvalidParameter(
                "id must contain 1 to 128 nonblank characters "
                "without null bytes."
            )

        try:
            self.repository.configure_transaction()
            analysis = self.repository.get_by_pix_id(pix_id)

            if analysis is None:
                raise PixAnalysisNotFound()

            body = PixAnalysisDTO.obj_to_dict(analysis)
            self.session.rollback()

            return body

        except (DBAPIError, DatabasePoolTimeout) as error:
            self._handle_database_error(error)
        except Exception:
            self.session.rollback()
            raise
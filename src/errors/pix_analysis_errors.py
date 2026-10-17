from errors.base_error import QIException


class PixAnalysisNotFound(QIException):
    code = "SNT001001"

    def __init__(self) -> None:
        super().__init__(
            title="Pix analysis not found",
            code=self.code,
            http_status=404,
            description="No analysis was found for this Pix ID.",
            translation="Nenhuma análise foi encontrada para este ID de Pix.",
        )


class PixIdConflict(QIException):
    code = "SNT001002"

    def __init__(self) -> None:
        super().__init__(
            title="Pix ID conflict",
            code=self.code,
            http_status=409,
            description="This Pix ID was already used with different data.",
            translation="Este ID de Pix já foi utilizado com dados diferentes.",
        )


class PixAnalysisUnavailable(QIException):
    code = "SNT001003"

    def __init__(self) -> None:
        super().__init__(
            title="Pix analysis temporarily unavailable",
            code=self.code,
            http_status=503,
            description="Retry later using the same Pix ID and payload.",
            translation="Tente novamente usando o mesmo ID de Pix e os mesmos dados.",
        )
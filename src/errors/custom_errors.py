from errors import QIException


class InvalidDocumentNumber(QIException):
    code = "QIT001003"

    def __init__(self, document_number) -> None:
        title = "Invalid Document Number"
        http_status = 422
        description = (
            f"The document number {document_number} is not a valid CPF."
        )
        translation = "O CPF informado não é válido."

        super().__init__(
            title,
            self.code,
            http_status,
            description,
            translation,
        )
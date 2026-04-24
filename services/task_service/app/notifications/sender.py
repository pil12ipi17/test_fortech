from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailMessage:
    recipient: str
    subject: str
    body: str


@dataclass(frozen=True)
class EmailSendResult:
    success: bool
    error_message: str | None = None


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> EmailSendResult:
        ...


class MockEmailSender:
    def send(self, message: EmailMessage) -> EmailSendResult:
        print(f"MockEmailSender sent to={message.recipient} subject={message.subject}")
        return EmailSendResult(success=True)
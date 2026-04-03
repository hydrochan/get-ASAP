"""출판사 파서 기저 클래스 (per D-07, D-08)"""
from abc import ABC, abstractmethod
from models import PaperMetadata


class BaseParser(ABC):
    """출판사별 메일 파서의 추상 기저 클래스.

    parsers/ 디렉토리에 이 클래스를 상속한 파일을 추가하면 자동 등록된다.
    각 구체 파서는 can_parse와 parse 두 메서드를 반드시 구현해야 한다.
    """

    publisher_name: str = ""  # 출판사 식별 이름 (서브클래스에서 오버라이드)

    @abstractmethod
    def can_parse(self, sender: str, subject: str) -> bool:
        """이 파서가 해당 메일을 처리할 수 있는지 판단.

        Args:
            sender: 메일 발신자 이메일 주소
            subject: 메일 제목

        Returns:
            처리 가능하면 True, 아니면 False
        """

    @abstractmethod
    def parse(self, message_body: str) -> list[PaperMetadata]:
        """메일 본문에서 논문 메타데이터 목록 추출.

        Args:
            message_body: 메일 본문 텍스트 (HTML 또는 plain text)

        Returns:
            추출된 PaperMetadata 목록 (논문이 없으면 빈 리스트)
        """

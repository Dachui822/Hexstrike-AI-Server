from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class TaskStore(ABC):
    @abstractmethod
    def create_task(self, task_id: str, data: Dict[str, Any]) -> bool:
        pass
    
    @abstractmethod
    def update_status(self, task_id: str, status: str, progress: float = 0.0, message: str = "") -> bool:
        pass
    
    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def list_tasks(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def cancel_task(self, task_id: str) -> bool:
        pass

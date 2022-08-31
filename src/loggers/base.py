from abc import ABC, abstractmethod
import os

class BaseLogger(ABC):
    def __init__(self, log_dir, exp_name) -> None:
        super().__init__()
        self.exp_name = exp_name
        self.log_dir = log_dir
        if not os.path.exists(f"{self.log_dir}/runlogs"):
            os.umask(0)
            os.makedirs(self.log_dir , mode=0o777, exist_ok=True)
            os.makedirs(f"{self.log_dir }/runlogs", mode=0o777, exist_ok=True)
            os.makedirs(f"{self.log_dir }/tblogs", mode=0o777, exist_ok=True)
        pass

    def log(self, logging_data):
        pass

    def log_env_train(self, metric_data):
        pass

    def log_env_val(self, metric_data):
        pass
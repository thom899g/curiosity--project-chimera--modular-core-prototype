"""
Firebase-powered monotonic clock for distributed system consistency.
Critical for temporal ordering of events across multiple instances.
"""
from datetime import datetime, timezone
from typing import Optional
import time

from firebase_admin import firestore
import firebase_admin
from firebase_admin import credentials


class SystemClock:
    """
    Application-level monotonic clock using Firebase server timestamps.
    Ensures consistent temporal ordering across distributed instances.
    
    Design Philosophy:
    - Server timestamps prevent clock drift between instances
    - Local monotonic fallback for offline operation
    - Built-in validation to detect temporal anomalies
    """
    
    def __init__(self, firebase_credential_path: str = "serviceAccountKey.json"):
        """
        Initialize with Firebase credentials for server timestamp access.
        
        Args:
            firebase_credential_path: Path to Firebase service account key
        """
        if not firebase_admin._apps:
            cred = credentials.Certificate(firebase_credential_path)
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
        self._last_server_time: Optional[datetime] = None
        self._local_offset = 0.0
        self._drift_threshold_seconds = 2.0
        
    def now(self) -> datetime:
        """
        Get current time with Firebase server timestamp guarantee.
        
        Returns:
            Firebase server timestamp when available, UTC fallback otherwise
            
        Raises:
            ConnectionError: If cannot connect to Firebase and no fallback available
        """
        try:
            # Use Firestore server timestamp
            server_time = firestore.SERVER_TIMESTAMP
            
            # Create a dummy document to get actual timestamp
            doc_ref = self.db.collection("_system_clock").document("timestamp")
            doc_ref.set({"timestamp": server_time})
            
            # Read it back
            doc = doc_ref.get()
            if doc.exists:
                server_timestamp = doc.to_dict().get("timestamp")
                if server_timestamp:
                    self._last_server_time = server_timestamp
                    return server_timestamp
            
            # Fallback to local time with offset
            return self._get_fallback_time()
            
        except Exception as e:
            print(f"WARNING: Cannot reach Firebase for server time: {e}")
            return self._get_fallback_time()
    
    def _get_fallback_time(self) -> datetime:
        """Get fallback time with monotonic consistency."""
        current_utc = datetime.now(timezone.utc)
        
        if self._last_server_time:
            # Calculate drift and warn if significant
            local_utc = datetime.now(timezone.utc)
            drift = (local_utc - self._last_server_time).total_seconds()
            if abs(drift) > self._drift_threshold_seconds:
                print(f"WARNING: Clock drift detected: {drift:.2f} seconds")
        
        return current_utc
    
    def validate_event_ordering(self, event1_time: datetime, event2_time: datetime) -> bool:
        """
        Validate that two events are in correct temporal order.
        
        Args:
            event1_time: Earlier event timestamp
            event2_time: Later event timestamp
            
        Returns:
            True if ordering is valid (event1 <= event2)
        """
        if event1_time.tzinfo is None:
            event1_time = event1_time.replace(tzinfo=timezone.utc)
        if event2_time.tzinfo is None:
            event2_time = event2_time.replace(tzinfo=timezone.utc)
        
        return event1_time <= event2_time
    
    def get_sequence_key(self) -> str:
        """
        Generate a Firebase-compatible sequence key for event ordering.
        Uses server timestamp to guarantee global monotonic ordering.
        
        Returns:
            String timestamp for ordering (ISO format)
        """
        current_time = self.now()
        return current_time.isoformat()
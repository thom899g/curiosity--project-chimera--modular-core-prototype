"""
Canonical Event Schema for distributed event log.
All system communication flows through this event structure.
"""
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum
import uuid

from pydantic import BaseModel, Field, validator, root_validator


class EventType(str, Enum):
    """All possible event types in the system - exhaustive for type safety."""
    MARKET_TICK = "MarketTick"
    MARKET_TICK_CORRECTION = "MarketTickCorrection"
    SIGNAL_GENERATED = "SignalGenerated"
    STRATEGY_EVALUATION = "StrategyEvaluation"
    CAPITAL_ALLOCATION = "CapitalAllocation"
    TRADE_EXECUTION = "TradeExecution"
    TELEMETRY = "Telemetry"
    SYSTEM_HEALTH = "SystemHealth"
    ERROR = "Error"
    KILL_SWITCH = "KillSwitch"


class CanonicalEvent(BaseModel):
    """
    Immutable event structure for all system communication.
    Enforces strict schema validation and temporal consistency.
    
    Design Principles:
    1. Events are immutable once created
    2. All events must have traceability (trace_id)
    3. Temporal ordering is enforced via sequence_key
    4. Schema versioning prevents breaking changes
    """
    
    # Core identification
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique event identifier"
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier for cross-module tracking"
    )
    sequence_key: str = Field(
        ...,
        description="Firebase-compatible key for temporal ordering"
    )
    
    # Event metadata
    event_type: EventType = Field(
        ...,
        description="Type of event from enumerated list"
    )
    created_at: datetime = Field(
        ...,
        description="Event creation timestamp (UTC)"
    )
    deadline_ms: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum allowed processing time in milliseconds"
    )
    
    # Content
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific data payload"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="System metadata (source, destination, priority)"
    )
    
    # Schema control
    version: str = Field(
        default="1.0.0",
        regex=r'^\d+\.\d+\.\d+$',
        description="Semantic version of event schema"
    )
    
    # Validation rules
    @validator('trace_id')
    def validate_trace_id(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("trace_id cannot be empty")
        return v.strip()
    
    @validator('sequence_key')
    def validate_sequence_key(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("sequence_key cannot be empty")
        return v.strip()
    
    @validator('created_at', pre=True)
    def validate_created_at(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                pass
        return v
    
    @root_validator(pre=True)
    def set_metadata_defaults(cls, values):
        """Ensure metadata contains required fields."""
        metadata = values.get('metadata', {})
        
        # Ensure source is set
        if 'source' not in metadata:
            metadata['source'] = 'unknown'
            
        # Set processing timestamp if not present
        if 'processed_at' not in metadata:
            metadata['processed_at'] = None
            
        values['metadata'] = metadata
        return values
    
    def is_expired(self, current_time: datetime) -> bool:
        """
        Check if event has exceeded its processing deadline.
        
        Args:
            current_time: Current system time
            
        Returns:
            True if event has expired
        """
        elapsed = (current_time - self.created_at).total_seconds() * 1000
        return elapsed > self.deadline_ms
    
    def to_firestore_dict(self) -> Dict[str, Any]:
        """
        Convert to Firestore-compatible dictionary.
        Handles datetime serialization and enum conversion.
        
        Returns:
            Dictionary ready for Firestore storage
        """
        data = self.dict()
        data['event_type'] = self.event_type.value
        data['created_at'] = self.created_at.isoformat()
        
        # Remove None values for Firestore compatibility
        return {k: v for k, v in data.items() if v is not None}
    
    @classmethod
    def from_firestore_dict(cls, data: Dict[str, Any]) -> 'CanonicalEvent':
        """
        Create event from Firestore document.
        Handles type conversions and validation.
        
        Args:
            data: Firestore document data
            
        Returns:
            Validated CanonicalEvent instance
        """
        # Convert event_type string to enum
        if 'event_type' in data and isinstance(data['event_type'], str):
            data['event_type'] = EventType(data['event_type'])
            
        # Parse created_at string if present
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(
                data['created_at'].replace('Z', '+00:00')
            )
        
        return cls(**data)
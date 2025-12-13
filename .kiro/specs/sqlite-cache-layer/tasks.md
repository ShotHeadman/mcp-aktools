# Implementation Plan

- [x] 1. Update configuration and add database settings





  - [x] 1.1 Add DATABASE_URL to Settings class in config.py


    - Add `database_url: Optional[str] = None` field
    - Add default path logic using user's cache directory
    - _Requirements: 1.1, 1.2_
  - [x] 1.2 Update .env.example with database configuration


    - Add DATABASE_URL example and documentation
    - _Requirements: 1.1_

- [x] 2. Implement SQLite cache backend





  - [x] 2.1 Create SQLiteCacheBackend class in mcp_aktools/cache/sqlite_backend.py


    - Implement `__init__` with database path handling and table creation
    - Implement `get`, `set`, `delete` methods
    - Implement `delete_by_prefix` and `clear` methods
    - Implement `get_stats` method
    - Use threading.Lock for thread safety
    - _Requirements: 1.3, 1.4, 2.1, 2.2, 5.1, 5.2, 5.3_
  - [ ]* 2.2 Write property test for SQLite backend operations
    - **Property 2: Cache Key Uniqueness**
    - **Validates: Requirements 2.2**

- [x] 3. Implement DataFrame serialization utilities





  - [x] 3.1 Create serialization module in mcp_aktools/cache/serialization.py


    - Implement `serialize_dataframe` using Parquet format
    - Implement `deserialize_dataframe` with error handling
    - Add pyarrow dependency to pyproject.toml
    - _Requirements: 2.1, 2.3, 2.4_
  - [ ]* 3.2 Write property test for DataFrame serialization round trip
    - **Property 1: DataFrame Serialization Round Trip**
    - **Validates: Requirements 2.3, 2.4**

- [x] 4. Checkpoint - Ensure all tests pass





  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement DividendChecker





  - [x] 5.1 Create DividendChecker class in mcp_aktools/cache/dividend_checker.py


    - Implement `__init__` with cache backend and TTL configuration
    - Implement `get_latest_ex_dividend_date` using stock_history_dividend_detail API
    - Implement `should_invalidate_cache` logic
    - Implement `invalidate_stock_cache` method
    - Cache dividend data with 24-hour TTL
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
  - [ ]* 5.2 Write property test for dividend TTL application
    - **Property 5: Dividend TTL Application**
    - **Validates: Requirements 4.2**
  - [ ]* 5.3 Write property test for cache invalidation on new dividend
    - **Property 6: Cache Invalidation on New Dividend**
    - **Validates: Requirements 4.3, 4.4**

- [x] 6. Update CacheKey class to use SQLite backend





  - [x] 6.1 Refactor CacheKey class in mcp_aktools/cache.py


    - Add class-level SQLite backend singleton
    - Add class-level DividendChecker singleton
    - Update `get` method to check SQLite after memory miss
    - Update `set` method to store in both memory and SQLite
    - Update `delete` method to remove from both caches
    - Add `init_sqlite_backend` class method
    - Maintain backward compatibility with existing interface
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 6.1, 6.2, 6.3_
  - [ ]* 6.2 Write property test for memory cache population on SQLite hit
    - **Property 3: Memory Cache Population on SQLite Hit**
    - **Validates: Requirements 3.3**
  - [ ]* 6.3 Write property test for dual storage on API fetch
    - **Property 4: Dual Storage on API Fetch**
    - **Validates: Requirements 3.5**

- [ ] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Implement cache clearing functionality
  - [ ] 8.1 Add cache clearing methods to CacheKey class
    - Implement `clear_by_symbol` class method
    - Implement `clear_all` class method
    - Ensure both memory and SQLite caches are cleared
    - _Requirements: 8.1, 8.2, 8.3_
  - [ ]* 8.2 Write property test for selective cache clearing
    - **Property 8: Selective Cache Clearing**
    - **Validates: Requirements 8.1**
  - [ ]* 8.3 Write property test for complete cache clearing
    - **Property 9: Complete Cache Clearing**
    - **Validates: Requirements 8.2**
  - [ ]* 8.4 Write property test for dual cache clearing
    - **Property 10: Dual Cache Clearing**
    - **Validates: Requirements 8.3**

- [ ] 9. Implement logging and statistics
  - [ ] 9.1 Add logging to cache operations
    - Log cache hits at DEBUG level
    - Log cache misses at DEBUG level
    - Log database errors at ERROR level
    - _Requirements: 7.1, 7.2, 7.3_
  - [ ] 9.2 Implement cache statistics tracking
    - Track hit count, miss count, and entry count
    - Implement `get_statistics` method
    - _Requirements: 7.4_
  - [ ]* 9.3 Write property test for statistics accuracy
    - **Property 11: Statistics Accuracy**
    - **Validates: Requirements 7.4**

- [ ] 10. Integrate with AkShareClient
  - [ ] 10.1 Update AkShareClient to initialize SQLite backend
    - Call `CacheKey.init_sqlite_backend()` in client initialization
    - Pass database path from settings
    - _Requirements: 1.1, 1.2_
  - [ ] 10.2 Add dividend check integration for price data queries
    - Check for new dividends before returning cached price data
    - Invalidate cache if new dividend detected
    - _Requirements: 4.1, 4.3_

- [ ] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 12. Write thread safety property test
  - [ ]* 12.1 Write property test for concurrent access
    - **Property 7: Thread-Safe Concurrent Access**
    - **Validates: Requirements 5.1**

- [ ] 13. Remove diskcache dependency
  - [ ] 13.1 Remove diskcache from cache.py
    - Remove diskcache import and usage
    - Update CacheKey to use only memory + SQLite
    - _Requirements: 6.2_
  - [ ] 13.2 Update pyproject.toml
    - Remove diskcache from dependencies
    - Add pyarrow if not already present
    - _Requirements: 6.2_

- [ ] 14. Final Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

# Requirements Document

## Introduction

本文档定义了为 MCP AkTools 项目添加 SQLite 数据库缓存层的需求。由于 AkShare 的东方财富接口增加了反爬机制，频繁请求会被服务端拦截。通过引入 SQLite 持久化缓存，可以将查询结果存储到本地数据库，下次查询相同数据时优先从数据库获取，大大减少对外部 API 的请求频率，提高系统稳定性和响应速度。

核心设计理念：历史股票数据（日线、周线、分时等）本身不会改变，因此采用永久缓存策略，不设置 TTL 过期时间。只有在发生分红送股等除权事件时，才需要主动失效相关缓存。

## Glossary

- **SQLite_Cache**: 基于 SQLite 数据库的持久化缓存系统，用于存储 API 查询结果
- **Cache_Key**: 缓存键，由函数名和参数组合生成的唯一标识符
- **Memory_Cache**: 内存缓存，使用 TTLCache 实现的快速访问缓存层
- **Permanent_Cache**: 永久缓存，不设置过期时间，数据永久有效直到被主动失效
- **Cache_Invalidation**: 缓存失效，当发生除权除息等事件时主动清除相关缓存
- **Corporate_Action**: 公司行为，包括分红、送股、配股等会影响历史价格的事件
- **Ex_Dividend_Date (除权除息日)**: 股票除权除息的日期，该日期后前复权价格会发生变化
- **stock_history_dividend_detail**: AkShare 提供的接口，用于获取单个股票的分红历史，返回公告日期、送股、转增、派息、除权除息日等信息
- **DataFrame**: pandas 库的数据结构，用于存储表格数据

## Requirements

### Requirement 1

**User Story:** As a developer, I want to configure the SQLite database path via environment variables, so that I can customize the cache storage location.

#### Acceptance Criteria

1. WHEN the application starts THEN the SQLite_Cache SHALL read the database path from the `DATABASE_URL` environment variable
2. WHEN the `DATABASE_URL` environment variable is not set THEN the SQLite_Cache SHALL use a default path in the user's cache directory
3. WHEN the database path is configured THEN the SQLite_Cache SHALL create the database file and parent directories if they do not exist
4. WHEN the database connection is established THEN the SQLite_Cache SHALL create the required tables if they do not exist

### Requirement 2

**User Story:** As a system, I want to store API query results in SQLite database permanently, so that historical data never needs to be re-fetched.

#### Acceptance Criteria

1. WHEN a cache entry is stored THEN the SQLite_Cache SHALL serialize the DataFrame to a storable format
2. WHEN a cache entry is stored THEN the SQLite_Cache SHALL record the cache key, serialized data, and creation timestamp
3. WHEN serializing a DataFrame THEN the SQLite_Cache SHALL preserve all column types and data integrity
4. WHEN deserializing cached data THEN the SQLite_Cache SHALL restore the original DataFrame structure
5. WHEN storing historical price data THEN the SQLite_Cache SHALL store the data permanently without expiration

### Requirement 3

**User Story:** As a system, I want to implement a two-tier cache hierarchy (Memory → SQLite → API), so that data retrieval is optimized for both speed and persistence.

#### Acceptance Criteria

1. WHEN retrieving data THEN the SQLite_Cache SHALL first check the memory cache
2. WHEN memory cache misses THEN the SQLite_Cache SHALL check the SQLite database
3. WHEN SQLite cache hits THEN the SQLite_Cache SHALL populate the memory cache with the retrieved data
4. WHEN both memory and SQLite caches miss THEN the SQLite_Cache SHALL call the external API
5. WHEN API returns data THEN the SQLite_Cache SHALL store the data in both memory cache and SQLite database

### Requirement 4

**User Story:** As a system, I want to invalidate cache when corporate actions occur, so that forward-adjusted price data remains accurate.

#### Acceptance Criteria

1. WHEN a corporate action query is made THEN the SQLite_Cache SHALL use `stock_history_dividend_detail(symbol, indicator='分红')` API to check for dividend events
2. WHEN querying corporate actions THEN the SQLite_Cache SHALL cache the result with a 24-hour TTL to minimize API calls
3. WHEN a new ex-dividend date (除权除息日) is detected that is more recent than the cached record THEN the SQLite_Cache SHALL invalidate all cached price data for that stock
4. WHEN invalidating cache THEN the SQLite_Cache SHALL remove entries matching the stock symbol from the database
5. WHEN the corporate action cache TTL expires THEN the SQLite_Cache SHALL re-query the `stock_history_dividend_detail` API

### Requirement 5

**User Story:** As a developer, I want the SQLite cache to be thread-safe, so that concurrent requests do not cause data corruption.

#### Acceptance Criteria

1. WHEN multiple threads access the cache simultaneously THEN the SQLite_Cache SHALL use appropriate locking mechanisms
2. WHEN writing to the database THEN the SQLite_Cache SHALL use transactions to ensure data consistency
3. WHEN a database operation fails THEN the SQLite_Cache SHALL rollback the transaction and log the error

### Requirement 6

**User Story:** As a developer, I want to maintain backward compatibility with the existing cache interface, so that existing code continues to work without modification.

#### Acceptance Criteria

1. WHEN the CacheKey class is used THEN the SQLite_Cache SHALL provide the same `get`, `set`, and `delete` methods
2. WHEN migrating from diskcache to SQLite THEN the SQLite_Cache SHALL support permanent storage mode
3. WHEN the cache is initialized THEN the SQLite_Cache SHALL accept the same parameters as the current implementation

### Requirement 7

**User Story:** As a system administrator, I want to monitor cache performance, so that I can optimize cache configuration.

#### Acceptance Criteria

1. WHEN a cache hit occurs THEN the SQLite_Cache SHALL log the event at DEBUG level with the cache key
2. WHEN a cache miss occurs THEN the SQLite_Cache SHALL log the event at DEBUG level with the cache key
3. WHEN a database error occurs THEN the SQLite_Cache SHALL log the error at ERROR level with full exception details
4. WHEN cache statistics are requested THEN the SQLite_Cache SHALL provide hit rate, miss rate, and entry count

### Requirement 8

**User Story:** As a developer, I want to manually clear cache for specific stocks or all data, so that I can handle edge cases and maintenance tasks.

#### Acceptance Criteria

1. WHEN a clear cache command is issued for a specific stock THEN the SQLite_Cache SHALL remove all entries for that stock symbol
2. WHEN a clear all cache command is issued THEN the SQLite_Cache SHALL remove all entries from the database
3. WHEN cache is cleared THEN the SQLite_Cache SHALL also clear the corresponding memory cache entries


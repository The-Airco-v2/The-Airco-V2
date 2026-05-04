type ByteArray = number[] | Uint8Array;
/**
 * Apply a delta byte array to a source byte array, returning the target byte array.
 */
export declare function applyDelta<T extends ByteArray>(source: T, delta: T): T;
export {};

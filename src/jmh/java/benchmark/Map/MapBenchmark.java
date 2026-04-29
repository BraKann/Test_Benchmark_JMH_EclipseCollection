package benchmark.Map;

import org.eclipse.collections.api.map.ImmutableMap;
import org.eclipse.collections.api.map.MutableMap;
import org.eclipse.collections.api.map.primitive.MutableIntIntMap;
import org.eclipse.collections.api.map.primitive.MutableIntObjectMap;
import org.eclipse.collections.impl.map.mutable.UnifiedMap;
import org.eclipse.collections.impl.map.mutable.primitive.IntIntHashMap;
import org.eclipse.collections.impl.map.mutable.primitive.IntObjectHashMap;
import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
@Warmup(iterations = 2, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(1)
public class MapBenchmark {

    @Param({"1000", "10000", "100000", "1000000"})
    public int size;

    private UnifiedMap<Integer, Integer> unifiedMap;
    private ImmutableMap<Integer, Integer> immutableMap;
    private IntIntHashMap intIntMap;
    private IntObjectHashMap<Integer> intObjectMap;

    @Setup(Level.Trial)
    public void setup() {
        unifiedMap = new UnifiedMap<>(size * 2);
        intIntMap = new IntIntHashMap(size * 2);
        intObjectMap = new IntObjectHashMap<>(size * 2);
        for (int i = 0; i < size; i++) {
            int value = i * 3;
            unifiedMap.put(i, value);
            intIntMap.put(i, value);
            intObjectMap.put(i, value);
        }
        immutableMap = unifiedMap.toImmutable();
    }

    @Benchmark
    public UnifiedMap<Integer, Integer> insert_unifiedMap() {
        UnifiedMap<Integer, Integer> map = new UnifiedMap<>(size * 2);
        for (int i = 0; i < size; i++) {
            map.put(i, i * 3);
        }
        return map;
    }

    @Benchmark
    public ImmutableMap<Integer, Integer> insert_immutableMap() {
        UnifiedMap<Integer, Integer> map = new UnifiedMap<>(size * 2);
        for (int i = 0; i < size; i++) {
            map.put(i, i * 3);
        }
        return map.toImmutable();
    }

    @Benchmark
    public IntIntHashMap insert_intIntHashMap() {
        IntIntHashMap map = new IntIntHashMap(size * 2);
        for (int i = 0; i < size; i++) {
            map.put(i, i * 3);
        }
        return map;
    }

    @Benchmark
    public IntObjectHashMap<Integer> insert_intObjectHashMap() {
        IntObjectHashMap<Integer> map = new IntObjectHashMap<>(size * 2);
        for (int i = 0; i < size; i++) {
            map.put(i, i * 3);
        }
        return map;
    }

    @Benchmark
    public void traverse_unifiedMap_forEachKeyValue(Blackhole blackhole) {
        unifiedMap.forEachKeyValue((key, value) -> {
            blackhole.consume(key);
            blackhole.consume(value);
        });
    }

    @Benchmark
    public void traverse_immutableMap_forEachKeyValue(Blackhole blackhole) {
        immutableMap.forEachKeyValue((key, value) -> {
            blackhole.consume(key);
            blackhole.consume(value);
        });
    }

    @Benchmark
    public void traverse_intIntHashMap_forEachKeyValue(Blackhole blackhole) {
        intIntMap.forEachKeyValue((key, value) -> {
            blackhole.consume(key);
            blackhole.consume(value);
        });
    }

    @Benchmark
    public void traverse_intObjectHashMap_forEachKeyValue(Blackhole blackhole) {
        intObjectMap.forEachKeyValue((key, value) -> {
            blackhole.consume(key);
            blackhole.consume(value);
        });
    }

    @Benchmark
    public long traverse_unifiedMap_valuesSum() {
        long sum = 0L;
        for (Integer value : unifiedMap.values()) {
            sum += value;
        }
        return sum;
    }

    @Benchmark
    public long traverse_intIntHashMap_sum() {
        return intIntMap.sum();
    }

    @Benchmark
    public boolean search_unifiedMap_containsKey() {
        return unifiedMap.containsKey(size / 2);
    }

    @Benchmark
    public boolean search_immutableMap_containsKey() {
        return immutableMap.containsKey(size / 2);
    }

    @Benchmark
    public boolean search_intIntHashMap_containsKey() {
        return intIntMap.containsKey(size / 2);
    }

    @Benchmark
    public int search_unifiedMap_get() {
        Integer value = unifiedMap.get(size / 2);
        return value == null ? -1 : value;
    }

    @Benchmark
    public int search_immutableMap_get() {
        Integer value = immutableMap.get(size / 2);
        return value == null ? -1 : value;
    }

    @Benchmark
    public int search_unifiedMap_getIfAbsent() {
        return unifiedMap.getIfAbsent(size / 2, () -> -1);
    }

    @Benchmark
    public int search_intIntHashMap_get() {
        return intIntMap.get(size / 2);
    }

    @Benchmark
    public int search_intObjectHashMap_get() {
        Integer value = intObjectMap.get(size / 2);
        return value == null ? -1 : value;
    }

    @Benchmark
    public MutableMap<Integer, Integer> delete_unifiedMap_reject() {
        final int target = size / 2;
        return unifiedMap.reject((key, value) -> key == target);
    }

    @Benchmark
    public ImmutableMap<Integer, Integer> delete_immutableMap_reject() {
        final int target = size / 2;
        return immutableMap.reject((key, value) -> key == target);
    }

    @Benchmark
    public MutableIntIntMap delete_intIntHashMap_reject() {
        final int target = size / 2;
        return intIntMap.reject((key, value) -> key == target);
    }

    @Benchmark
    public MutableIntObjectMap<Integer> delete_intObjectHashMap_reject() {
        final int target = size / 2;
        return intObjectMap.reject((key, value) -> key == target);
    }
}
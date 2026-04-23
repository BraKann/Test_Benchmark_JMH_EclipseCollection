package benchmark.Set;

import org.eclipse.collections.api.set.ImmutableSet;
import org.eclipse.collections.api.set.MutableSet;
import org.eclipse.collections.api.set.primitive.MutableIntSet;
import org.eclipse.collections.impl.set.mutable.UnifiedSet;
import org.eclipse.collections.impl.set.mutable.primitive.IntHashSet;
import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(2)
public class SetBenchmark {

    @Param({"1000", "10000", "100000", "1000000"})
    public int size;

    private UnifiedSet<Integer> unifiedSet;
    private ImmutableSet<Integer> immutableSet;
    private IntHashSet intHashSet;

    @Setup(Level.Trial)
    public void setup() {
        unifiedSet = new UnifiedSet<>(size * 2);
        intHashSet = new IntHashSet(size * 2);
        immutableSet = unifiedSet.toImmutable();
        for (int i = 0; i < size; i++) {
            unifiedSet.add(i);
            intHashSet.add(i);
        }


    }

    @Benchmark
    public UnifiedSet<Integer> insert_unifiedSet() {
        UnifiedSet<Integer> set = new UnifiedSet<>(size * 2);
        for (int i = 0; i < size; i++) {
            set.add(i);
        }
        return set;
    }

    @Benchmark
    public ImmutableSet<Integer> insert_immutableSet() {
        UnifiedSet<Integer> set = new UnifiedSet<>(size * 2);
        for (int i = 0; i < size; i++) {
            set.add(i);
        }
        return set.toImmutable();
    }

    @Benchmark
    public IntHashSet insert_intHashSet() {
        IntHashSet set = new IntHashSet(size * 2);
        for (int i = 0; i < size; i++) {
            set.add(i);
        }
        return set;
    }

    @Benchmark
    public void traverse_unifiedSet_forEach(Blackhole blackhole) {
        unifiedSet.forEach(blackhole::consume);
    }

    @Benchmark
    public void traverse_immutableSet_forEach(Blackhole blackhole) {
        immutableSet.forEach(blackhole::consume);
    }

    @Benchmark
    public void traverse_intHashSet_forEach(Blackhole blackhole) {
        intHashSet.forEach(blackhole::consume);
    }

    @Benchmark
    public long traverse_unifiedSet_injectInto() {
        return unifiedSet.injectInto(0L, (long acc, Integer value) -> acc + value);
    }

    @Benchmark
    public long traverse_immutableSet_injectInto() {
        return immutableSet.injectInto(0L, (long acc, Integer value) -> acc + value);
    }

    @Benchmark
    public long traverse_intHashSet_sum() {
        return intHashSet.sum();
    }

    @Benchmark
    public boolean search_unifiedSet_contains() {
        return unifiedSet.contains(size / 2);
    }

    @Benchmark
    public boolean search_immutableSet_contains() {
        return immutableSet.contains(size / 2);
    }

    @Benchmark
    public boolean search_intHashSet_contains() {
        return intHashSet.contains(size / 2);
    }

    @Benchmark
    public boolean search_unifiedSet_anySatisfy() {
        return unifiedSet.anySatisfy(value -> value == size / 2);
    }

    @Benchmark
    public boolean search_intHashSet_anySatisfy() {
        return intHashSet.anySatisfy(value -> value == size / 2);
    }

    @Benchmark
    public MutableSet<Integer> delete_unifiedSet_reject() {
        final int target = size / 2;
        return unifiedSet.reject(value -> value == target);
    }

    @Benchmark
    public ImmutableSet<Integer> delete_immutableSet_reject() {
        final int target = size / 2;
        return immutableSet.reject(value -> value == target);
    }

    @Benchmark
    public MutableIntSet delete_intHashSet_reject() {
        final int target = size / 2;
        return intHashSet.reject(value -> value == target);
    }
}

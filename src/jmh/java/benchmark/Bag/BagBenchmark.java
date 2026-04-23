package benchmark.Bag;

import org.eclipse.collections.api.bag.ImmutableBag;
import org.eclipse.collections.api.bag.MutableBag;
import org.eclipse.collections.impl.bag.mutable.HashBag;
import org.openjdk.jmh.annotations.*;
import org.openjdk.jmh.infra.Blackhole;

import java.util.concurrent.TimeUnit;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
@Warmup(iterations = 5, time = 1)
@Measurement(iterations = 5, time = 1)
@Fork(2)
public class BagBenchmark {

    @Param({"1000", "10000", "100000", "1000000"})
    public int size;

    private HashBag<Integer> hashBag;
    private ImmutableBag<Integer> immutableBag;
    private int distinctValues;

    @Setup(Level.Trial)
    public void setup() {
        distinctValues = Math.max(1, Math.min(size, 1024));
        hashBag = new HashBag<>(size);
        immutableBag = hashBag.toImmutable();
        for (int i = 0; i < size; i++) {
            hashBag.add(i % distinctValues);
        }


    }

    @Benchmark
    public HashBag<Integer> insert_hashBag() {
        HashBag<Integer> bag = new HashBag<>(size);
        for (int i = 0; i < size; i++) {
            bag.add(i % distinctValues);
        }
        return bag;
    }

    @Benchmark
    public ImmutableBag<Integer> insert_immutableBag() {
        HashBag<Integer> bag = new HashBag<>(size);
        for (int i = 0; i < size; i++) {
            bag.add(i % distinctValues);
        }
        return bag.toImmutable();
    }

    @Benchmark
    public void traverse_hashBag_forEach(Blackhole blackhole) {
        hashBag.forEach(blackhole::consume);
    }

    @Benchmark
    public void traverse_hashBag_forEachWithOccurrences(Blackhole blackhole) {
        hashBag.forEachWithOccurrences((value, occurrences) -> {
            blackhole.consume(value);
            blackhole.consume(occurrences);
        });
    }

    @Benchmark
    public void traverse_immutableBag_forEach(Blackhole blackhole) {
        immutableBag.forEach(blackhole::consume);
    }

    @Benchmark
    public int search_hashBag_occurrencesOf() {
        return hashBag.occurrencesOf(distinctValues / 2);
    }

    @Benchmark
    public int search_immutableBag_occurrencesOf() {
        return immutableBag.occurrencesOf(distinctValues / 2);
    }

    @Benchmark
    public boolean search_hashBag_contains() {
        return hashBag.contains(distinctValues / 2);
    }

    @Benchmark
    public MutableBag<Integer> delete_hashBag_reject() {
        final int target = distinctValues / 2;
        return hashBag.reject(value -> value == target);
    }

    @Benchmark
    public Object delete_immutableBag_reject() {
        final int target = distinctValues / 2;
        return immutableBag.reject(value -> value == target);
    }
}

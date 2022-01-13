#!/bin/bash

n_runs=3

run_name=$1

for (( i = 0 ; i < $n_runs ; i++ ))
do
    sbatch scripts/train_gan.sh "${run_name}_csab/$i" "csab"
    sbatch scripts/train_gan.sh "${run_name}_pine/$i" "pine"
    sbatch scripts/train_gan.sh "${run_name}_naive/$i" "naive"
done

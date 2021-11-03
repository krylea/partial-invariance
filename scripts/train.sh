#!/bin/bash
#SBATCH --job-name=test
#SBATCH --output=logs/slurm-%j.txt
#SBATCH --open-mode=append
#SBATCH --ntasks=1
#SBATCH --gres=gpu:2
#SBATCH --partition=t4v1,t4v2,p100
#SBATCH --cpus-per-gpu=1
#SBATCH --mem=25GB
#SBATCH --exclude=gpu109

run_name=$1
target=$2
data=$3
num_inds=$4
equi=$5
is=$6
lts=$7
hs=$8
model=$9

if [ $target == "w1" ]
then
    argstring="--normalize scale --blur 0.001 --scaling 0.98 --lr 1e-3"
elif [ $target == "w2" ]
then
    argstring="--normalize scale --lr 1e-3"
elif [ $target == "w1_exact" ]
then
    argstring="--normalize scale --lr 1e-3"
elif [ $target == "kl" ]
then
    argstring="--normalize whiten --lr 1e-3"
fi

if [ $model == 'pine' ]
then
    argstring="${argstring} --model pine"
fi

if [ $equi -eq 1 ]
then
    argstring="${argstring} --equi"
fi

python3 train.py $run_name --target $target --data $data --num_inds $num_inds --dim $is --latent_size $lts --hidden_size $hs --checkpoint_name $SLURM_JOB_ID $argstring 
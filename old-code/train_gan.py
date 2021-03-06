import torch
import torchvision
import torch.nn as nn

from train_omniglot import ConvEncoder, ConvBlock, ConvLayer, MultiSetImageModel
from md_generator import MetaDatasetGenerator
from meta_dataset.dataset_spec import Split
from models2 import *
from generators import DistinguishabilityGenerator

import argparse
import os
import tqdm
import math

def train_adv(discriminator, generator, d_opt, g_opt, dataset, steps, device, set_size=(10,15),batch_size=64, save_every=2000, checkpoint_dir=None, data_kwargs={}):
    d_losses = []
    g_losses = []
    step=0
    if checkpoint_dir is not None:
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        else:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                load_dict = torch.load(checkpoint_path)
                discriminator, generator, d_opt, g_opt, step, d_losses, g_losses = load_dict['discriminator'], load_dict['generator'], load_dict['d_opt'], load_dict['g_opt'], load_dict['step'], load_dict['d_losses'], load_dict['g_losses']
    
    criterion = nn.BCEWithLogitsLoss()
    ones = torch.ones(batch_size).to(device)
    zeros = torch.zeros(batch_size).to(device)  #can these just always be the same tensors? does that break anything?
    while step < steps:
        for batch in dataset:
            d_opt.zero_grad()
            outputs = discriminator(batch.to(device))
            d_loss1 = criterion(outputs, ones)
            d_loss1.backward()

            nsamples = torch.randint(*set_size, (1,))
            noise = torch.randn(batch_size * n_samples, 1, 1).to(device)
            fake_batch = generator(noise).view(batch_size, n_samples, -1)

            outputs2 = discriminator(fake_batch.detach())
            d_loss2 = criterion(outputs2, zeros)
            d_loss2.backward()
            d_opt.step()

            g_opt.zero_grad()
            outputs3 = discriminator(fake_batch)
            g_loss = criterion(outputs3, ones)
            g_loss.backward()
            g_opt.step()

            d_losses.append((d_loss1+d_loss2).item()/2)
            g_losses.append(g_loss.item())

            step += 1
            if step % save_every == 0 and checkpoint_dir is not None:
                checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
                if os.path.exists(checkpoint_path):
                    os.remove(checkpoint_path)
                torch.save({
                    'discriminator':discriminator, 
                    'generator':generator,
                    'd_opt':d_opt, 
                    'g_opt':g_opt, 
                    'step': i, 
                    'd_loss': d_losses,
                    'g_loss': g_losses
                    }, checkpoint_path)

    return discriminator, generator, d_losses, g_losses


def train_synth(model, optimizer, generator, steps, scheduler=None, batch_size=64, eval_every=500, eval_steps=200, save_every=100, checkpoint_dir=None, data_kwargs={}):
    train_losses = []
    eval_accs = []
    initial_step=0
    if checkpoint_dir is not None:
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        else:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                load_dict = torch.load(checkpoint_path)
                model, optimizer, initial_step, train_losses, eval_accs = load_dict['model'], load_dict['optimizer'], load_dict['step'], load_dict['losses'], load_dict['accs']
    
    loss_fct = nn.BCEWithLogitsLoss()
    for i in tqdm.tqdm(range(initial_step, steps)):
        optimizer.zero_grad()

        (X,Y), target = generator(batch_size, **data_kwargs)

        out = model(X,Y)
        loss = loss_fct(out.squeeze(-1), target)
        loss.backward()
        optimizer.step()
        if scheduler is not None:
            scheduler.step()

        train_losses.append(loss.item())

        if i % eval_every == 0 and i > 0:
            acc_i = eval_synth(model, generator, eval_steps, batch_size, data_kwargs)
            eval_accs.append(acc_i)

        if checkpoint_dir is not None and i % save_every == 0 and i > 0:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            torch.save({'model':model,'optimizer':optimizer, 'step': i, 'losses':train_losses, 'accs': eval_accs}, checkpoint_path)
    
    test_acc = eval_synth(model, generator, 5*eval_steps, batch_size, data_kwargs)
    return model, (train_losses, eval_accs, test_acc)
        

def eval_synth(model, generator, steps, batch_size, data_kwargs):
    with torch.no_grad():
        n_correct = 0
        for i in range(steps):
            (X,Y), target = generator(batch_size, **data_kwargs)
            out = model(X,Y).squeeze(-1)
            n_correct += ((out > 0) == target).sum().item()
    return n_correct / (batch_size * steps)



#@profile
def train_meta(model, optimizer, train_dataset, val_dataset, test_dataset, steps, scheduler=None, batch_size=64, eval_every=500, 
    eval_steps=100, save_every=100, episode_classes=100, episode_datasets=5, episode_length=250, checkpoint_dir=None, data_kwargs={}):
    train_losses = []
    eval_accs = []
    step=0
    if checkpoint_dir is not None:
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        else:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                load_dict = torch.load(checkpoint_path)
                model, optimizer, scheduler, step, train_losses, eval_accs = load_dict['model'], load_dict['optimizer'], load_dict['scheduler'], load_dict['step'], load_dict['losses'], load_dict['accs']
    
    n_episodes = math.ceil((steps - step) / episode_length)
    avg_loss = 0
    loss_fct = nn.BCEWithLogitsLoss()
    for _ in tqdm.tqdm(range(n_episodes)):
        episode = train_dataset.get_episode(episode_classes, episode_datasets)
        for i in range(episode_length):
            optimizer.zero_grad()

            (X,Y), target = episode(batch_size, **data_kwargs)

            out = model(X,Y)
            loss = loss_fct(out.squeeze(-1), target)
            loss.backward()
            optimizer.step()
            if scheduler is not None:
                scheduler.step()

            avg_loss += loss.item()
            train_losses.append(loss.item())

            step += 1
                
            if step % save_every == 0 and step > 0:
                # save
                if checkpoint_dir is not None:
                    checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
                    if os.path.exists(checkpoint_path):
                        os.remove(checkpoint_path)
                    torch.save({'model':model,'optimizer':optimizer, 'scheduler':scheduler, 'step': step, 'losses':train_losses, 'accs': eval_accs}, checkpoint_path)

            if step >= steps:
                break
        else:
            acc = eval_disc(model, val_dataset.get_episode(episode_classes, episode_datasets), eval_steps, batch_size, data_kwargs)
            eval_accs.append(acc)
            avg_loss /= eval_every
            print("Step: %d\tLoss: %f\tAccuracy: %f" % (step, avg_loss, acc))
            avg_loss = 0

            continue
        break
    
    test_acc = eval_disc(model, test_dataset.get_episode(episode_classes, episode_datasets), 5*eval_steps, batch_size, data_kwargs)
    
    return model, (train_losses, eval_accs, test_acc)


def eval_disc(model, episode, steps, batch_size, data_kwargs, return_all=False):
    N = batch_size * steps
    with torch.no_grad():
        y,yhat,dl,sd=[],[],[],[]
        for i in range(steps):
            (X,Y), target, (dataset_level, same_dataset) = episode(batch_size, eval=True, **data_kwargs)
            out = model(X,Y).squeeze(-1)
            y.append(target)
            yhat.append(out>0)
            dl.append(dataset_level)
            sd.append(same_dataset)
            #n_correct += torch.eq((out > 0), target).sum().item()
        y=torch.cat(y, dim=0)
        yhat=torch.cat(yhat, dim=0)
        dl=torch.cat(dl, dim=0)
        sd=torch.cat(sd, dim=0)

    return summarize_eval(y, yhat, dl, sd, return_all)



def summarize_eval(y, yhat, dl, sd, return_all=False):
    N = y.size(0)
    correct = y==yhat
    acc = (y==yhat).sum().item() / N
    #prec = (y & yhat).sum().item() / yhat.sum().item()

    if not return_all:
        return acc

    def get_acc(labels):
        n = labels.sum().item()
        return (labels & correct).sum().item() / n, n

    dl_acc, n_dl = get_acc(dl)
    dl_pos_acc, n_dl_pos = get_acc(dl & y)
    dl_neg_acc, n_dl_neg = get_acc(dl & ~y)
    cl_acc, n_cl = get_acc(~dl)
    cl_pos_acc, n_cl_pos = get_acc(~dl & y)
    cl_neg_acc, n_cl_neg = get_acc(~dl & ~y)
    cl_neg_sd_acc, n_cl_neg_sd = get_acc(~dl & y & sd)
    cl_neg_dd_acc, n_cl_neg_dd = get_acc(~dl & ~y & ~sd)

    #dl_prec = (dl & y & yhat).sum().item() / (dl & yhat).sum().item()
    #cl_prec = (~dl & y & yhat).sum().item() / (~dl & yhat).sum().item()

    return (acc, dl_acc, dl_pos_acc, dl_neg_acc, cl_acc, cl_pos_acc, cl_neg_acc, cl_neg_sd_acc, cl_neg_dd_acc), (N, n_dl, n_dl_pos, n_dl_neg, n_cl, n_cl_pos, n_cl_neg, n_cl_neg_sd, n_cl_neg_dd)

'''
def eval_disc(model, dataset, steps, batch_size, episode_classes, episode_datasets, data_kwargs):
    def eval_episode(episode, **kwargs):
        n = batch_size * steps
        with torch.no_grad():
            y,yhat=[],[]
            for i in tqdm.tqdm(range(steps)):
                (X,Y), target = episode(batch_size, **kwargs)
                out = model(X,Y).squeeze(-1)
                y.append(target)
                yhat.append(out>0)
                #n_correct += torch.eq((out > 0), target).sum().item()
            y=torch.cat(y, dim=0).bool()
            yhat=torch.cat(yhat, dim=0)
            acc = torch.eq(y, yhat).sum().item() / n
            tp = torch.logical_and(y, yhat).sum().item()
            fp = torch.logical_and(y.logical_not(), yhat).sum().item()
            tn = torch.logical_and(y.logical_not(), yhat.logical_not()).sum().item()
            fn = torch.logical_and(y, yhat.logical_not()).sum().item()
        return acc, tp/(tp+fp) (tp/(tp+fn), tn/(fp+tn))
    episode = dataset.get_episode(episode_classes, episode_datasets)
    dataset_level_acc, dataset_level_prec, (dataset_level_pos_acc, dataset_level_neg_acc) = eval_episode(episode, p_dataset=1, **data_kwargs)
    aligned_acc, aligned_prec, (aligned_pos_acc, aligned_neg_acc) = eval_episode(episode, p_aligned=0, p_dataset=0, **data_kwargs)
'''    


'''
def train_gen(generator, discriminator, optimizer, train_dataset, steps, batch_size=64, save_every=2000, print_every=250, checkpoint_dir=None, data_kwargs={}):
    train_losses = []
    initial_step=1
    if checkpoint_dir is not None:
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        else:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                load_dict = torch.load(checkpoint_path)
                generator, discriminator, optimizer, initial_step, train_losses = load_dict['generator'], load_dict['discriminator'], load_dict['optimizer'], load_dict['step'], load_dict['losses']

    avg_loss = 0
    labels = torch.ones(batch_size)
    loss_fct = nn.BCEWithLogitsLoss()
    for i in tqdm.tqdm(range(steps)):
        optimizer.zero_grad()

        X = train_dataset(batch_size, **data_kwargs)
        noise = torch.randn(*X.size()).to(X.device)
        Y = generator(noise)

        out = discriminator(X,Y)
        loss = loss_fct(out.squeeze(-1), labels)
        loss.backward()
        optimizer.step()

        avg_loss += loss.item()
        train_losses.append(loss.item())

        if i % print_every == 0:
            avg_loss /= print_every
            print("Step: %d\tLoss: %f" % (i, avg_loss))
            avg_loss = 0

        if i % save_every == 0 and checkpoint_dir is not None:
            checkpoint_path = os.path.join(checkpoint_dir, "checkpoint.pt")
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            torch.save({'generator':generator, 'discriminator': discriminator, 'optimizer':optimizer, 'step': i, 'losses':train_losses}, checkpoint_path)
    
    return generator, train_losses   
'''


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_name', type=str)
    parser.add_argument('--model', type=str, default='csab', choices=['csab', 'naive', 'cross-only', 'pine', 'rn', 'naive-rn', 'naive-rff', 'union', 'union-enc'])
    parser.add_argument('--checkpoint_dir', type=str, default="/checkpoint/kaselby")
    parser.add_argument('--checkpoint_name', type=str, default=None)
    parser.add_argument('--num_blocks', type=int, default=1)
    parser.add_argument('--num_heads', type=int, default=4)
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--steps', type=int, default=16000)
    parser.add_argument('--dropout', type=float, default=0)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--latent_size', type=int, default=512)
    parser.add_argument('--hidden_size', type=int, default=1024)
    parser.add_argument('--set_size', type=int, nargs=2, default=[3,10])
    parser.add_argument('--basedir', type=str, default="final-runs2")
    parser.add_argument('--save_every', type=int, default=200)
    parser.add_argument('--eval_every', type=int, default=1000)
    parser.add_argument('--eval_steps', type=int, default=200)
    parser.add_argument('--episode_classes', type=int, default=100)
    parser.add_argument('--episode_datasets', type=int, default=5)
    parser.add_argument('--episode_length', type=int, default=500)
    parser.add_argument('--p_dl', type=float, default=0.3)
    parser.add_argument('--img_encoder', choices=['cnn','resnet'], default='cnn')
    parser.add_argument('--weight_sharing', type=str, choices=['none', 'cross', 'sym'], default='cross')
    parser.add_argument('--merge_type', type=str, default='concat', choices=['concat', 'sum', 'lambda'])
    parser.add_argument('--data', type=str, default='md', choices=['md', 'synth'])
    parser.add_argument('--n', type=int, default=8)
    parser.add_argument('--warmup_steps', type=int, default=1000)
    parser.add_argument('--ln', action='store_true')
    parser.add_argument('--decoder_layers', type=int, default=0)
    parser.add_argument('--dataset_path', type=str, default="/ssd003/projects/meta-dataset")
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    dataset_dir = "meta-dataset" if args.data == 'md' else "synth_" + str(args.n)
    run_dir = os.path.join(args.basedir, "distinguishability", dataset_dir, args.run_name)
    if not os.path.exists(run_dir):
        os.makedirs(run_dir)

    device = torch.device("cuda")

    input_size = args.latent_size if args.data == 'md' else args.n
    if args.model == 'csab':
        model_kwargs={
            'ln':args.ln,
            'remove_diag':False,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'equi':False,
            'weight_sharing': args.weight_sharing,
            'merge': args.merge_type,
            'decoder_layers': args.decoder_layers
        }
        set_model = MultiSetTransformer(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'cross-only':
        model_kwargs={
            'ln':args.ln,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'equi':False,
            'weight_sharing': args.weight_sharing,
            'decoder_layers': args.decoder_layers
        }
        set_model = CrossOnlyModel(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'naive':
        model_kwargs={
            'ln':args.ln,
            'remove_diag':False,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'equi':False,
            'weight_sharing': args.weight_sharing,
            'decoder_layers': args.decoder_layers
        }
        set_model = NaiveSetTransformer(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'pine':
        set_model = PINE(input_size, int(args.latent_size/4), 16, 2, 4*args.hidden_size, 1)
    elif args.model == 'rn':
        model_kwargs={
            'ln':args.ln,
            'remove_diag':False,
            'num_blocks':args.num_blocks,
            'dropout':args.dropout,
            'equi':False,
            'weight_sharing': args.weight_sharing,
            'pool1': 'max',
            'pool2': 'max',
            'decoder_layers': args.decoder_layers
        }
        set_model = MultiRNModel(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'naive-rn':
        model_kwargs={
            'ln':args.ln,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'equi':False,
            'pool': 'max',
            'decoder_layers': args.decoder_layers
        }
        set_model = NaiveRelationNetwork(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'naive-rff':
        model_kwargs={
            'ln':args.ln,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'equi':False,
            'decoder_layers': args.decoder_layers
        }
        set_model = NaiveRFF(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    elif args.model == 'union' or args.model == 'union-enc':
        model_kwargs={
            'ln':args.ln,
            'num_blocks':args.num_blocks,
            'num_heads':args.num_heads,
            'dropout':args.dropout,
            'set_encoding': args.model == 'union-enc'
        }
        set_model = UnionTransformer(input_size, args.latent_size, args.hidden_size, 1, **model_kwargs)
    else:
        raise NotImplementedError

    data_kwargs = {'set_size':args.set_size}
    if args.data == 'md':
        if args.img_encoder == "cnn":
            image_size=84
            layers = [
                ConvLayer(3, 32, kernel_size=7, stride=2),
                ConvBlock(32, 32, n_conv=2, pool='max'),
                ConvBlock(32, 64, n_conv=2,pool='max'),
                ConvBlock(64, 128, n_conv=2, pool='max')
            ]
            encoder = ConvEncoder(layers, image_size, args.latent_size)
        else:
            image_size=224
            encoder = torchvision.models.resnet101(pretrained=False)
            encoder.fc = nn.Linear(2048, args.latent_size)
        discriminator = MultiSetImageModel(encoder, set_model).to(device)

        train_generator = MetaDatasetGenerator(root_dir=args.dataset_path, image_size=image_size, split=Split.TRAIN, device=device)
        val_generator = MetaDatasetGenerator(root_dir=args.dataset_path, image_size=image_size, split=Split.VALID, device=device)
        test_generator = MetaDatasetGenerator(root_dir=args.dataset_path, image_size=image_size, split=Split.TEST, device=device)
        data_kwargs['p_dataset'] = args.p_dl
    else: 
        discriminator = set_model.to(device)
        train_generator = DistinguishabilityGenerator(device)
        data_kwargs['n'] = args.n
    
    batch_size = args.batch_size
    steps = args.steps
    eval_every=args.eval_every
    eval_steps=args.eval_steps
    episode_length = args.episode_length
    save_every=args.save_every
    if torch.cuda.device_count() > 1:
        n_gpus = torch.cuda.device_count()
        print("Let's use", n_gpus, "GPUs!")
        discriminator = nn.DataParallel(discriminator)
        batch_size *= n_gpus
        steps = int(steps/n_gpus)
        eval_every = int(eval_every/n_gpus)
        eval_steps = int(eval_steps/n_gpus)
        episode_length = int(episode_length / n_gpus)
        save_every = int(save_every / n_gpus)

    print("Beginning Training...")

    
    optimizer = torch.optim.Adam(discriminator.parameters(), args.lr)
    scheduler = torch.optim.lr_scheduler.LinearLR(optimizer, start_factor=1e-8, total_iters=args.warmup_steps) if args.warmup_steps > 0 else None
    checkpoint_dir = os.path.join(args.checkpoint_dir, args.checkpoint_name) if args.checkpoint_name is not None else None
    if args.data == 'md':
        discriminator, (losses, accs, test_acc) = train_meta(discriminator, optimizer, train_generator, val_generator, test_generator, steps, 
            scheduler=scheduler, batch_size=batch_size, checkpoint_dir=checkpoint_dir, data_kwargs=data_kwargs, eval_every=eval_every, eval_steps=eval_steps,
            episode_classes=args.episode_classes, episode_datasets=args.episode_datasets, episode_length=episode_length, save_every=save_every)
    else:
        discriminator, (losses, accs, test_acc) = train_synth(discriminator, optimizer, train_generator, steps, 
            scheduler=scheduler, batch_size=batch_size, checkpoint_dir=checkpoint_dir, data_kwargs=data_kwargs, eval_every=eval_every, eval_steps=eval_steps,
            save_every=save_every)

    print("Test Accuracy:", test_acc)

    model_out = discriminator._modules['module'] if torch.cuda.device_count() > 1 else discriminator
    torch.save(model_out, os.path.join(run_dir, "model.pt"))  
    torch.save({'losses':losses, 'eval_accs': accs, 'test_acc': test_acc, 'args':args}, os.path.join(run_dir,"logs.pt"))  





    

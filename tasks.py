
from builders import SET_MODEL_BUILDERS, CONV_MODEL_BUILDERS
from trainer import Trainer, CountingTrainer, CaptionTrainer, MetaDatasetTrainer, StatisticalDistanceTrainer, Pretrainer, DonskerVaradhanTrainer, DonskerVaradhanMITrainer#, DonskerVaradhanTrainer2
from datasets.counting import OmniglotCooccurenceGenerator, ImageCooccurenceGenerator, DatasetByClass, load_cifar, load_mnist, load_omniglot
from datasets.alignment import EmbeddingAlignmentGenerator, CaptionGenerator, load_coco_data, load_flickr_data, bert_tokenize_batch, fasttext_tokenize_batch, load_pairs, split_pairs
from datasets.distinguishability import DistinguishabilityGenerator
from datasets.meta_dataset import MetaDatasetGenerator, Split
from datasets.distributions import CorrelatedGaussianGenerator, GaussianGenerator, NFGenerator, StandardGaussianGenerator, CorrelatedGaussianGenerator2, LabelledGaussianGenerator, RandomEncoderGenerator, ProtectedDatasetGenerator
from models.task import ImageEncoderWrapper, BertEncoderWrapper, EmbeddingEncoderWrapper, MultiSetImageModel, MultiSetModel
from models.set import MultiSetTransformerEncoder, MultiSetTransformerEncoderDecoder
from utils import kl_mc, kl_mc_mixture, mi_corr_gaussian, kl_knn, kraskov_mi1, whiten_split, normalize_sets

import fasttext
from transformers import BertTokenizer, BertModel
import torchvision
import torch.nn as nn
import torch

import os
import math



class Task():
    pretraining_task=None
    trainer_cls=Trainer
    def __init__(self, args):
        self.args = args

    def build_model(self, pretrained_model=None):
        return SET_MODEL_BUILDERS[self.args.model](self.args)
    
    def build_dataset(self):
        pass
    
    def build_training_args(self):
        train_args = {
            'batch_size': self.args.batch_size,
            'grad_steps': self.args.grad_steps,
            'data_kwargs': {'set_size': self.args.set_size}
        }
        eval_args = {
            'batch_size': self.args.batch_size,
            'data_kwargs': {'set_size': self.args.set_size}
        }
        return train_args, eval_args

    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'ss_schedule': self.args.ss_schedule
        }
        return trainer_kwargs
    
    def build_trainer(self, model, optimizer, scheduler, train_dataset, val_dataset, test_dataset, device, logger, checkpoint_dir=None):
        train_args, eval_args = self.build_training_args()
        trainer_kwargs = self.build_trainer_kwargs()
        trainer = self.trainer_cls(model, optimizer, train_dataset, val_dataset, test_dataset, 
            train_args, eval_args, device, logger=logger, scheduler=scheduler, checkpoint_dir=checkpoint_dir, **trainer_kwargs)
        return trainer


#
#   Pretraining Task
#

class ImageClassificationTask(Task):
    n_classes={
        'mnist': 10,
        'cifar': 100,
        'omniglot': -1  #fill this in later
    }
    
    def build_model(self):
        encoder = CONV_MODEL_BUILDERS[self.args.dataset](self.args)
        model = nn.Sequential(encoder, nn.Linear(self.args.latent_size, self.n_classes[self.args.dataset]))
        return model
    
    def build_trainer(self, model, optimizer, train_dataset, val_dataset, test_dataset, device):
        trainer = Pretrainer(model, optimizer, train_dataset, val_dataset, test_dataset, device, self.args.batch_size, eval_every=-1)
        return trainer

        

#
#   Alignment Tasks
#

class EmbeddingTask(Task):
    def build_dataset(self):
        src_emb = fasttext.load_model(os.path.join(self.args.dataset_dir, "fasttext", "cc.en.300.bin"))
        tgt_emb = fasttext.load_model(os.path.join(self.args.dataset_dir, "fasttext", "cc.fr.300.bin"))
        pairs = load_pairs(os.path.join(self.args.dataset_dir, "fasttext", "valid_en-fr.txt"))
        train_pairs, val_pairs, test_pairs = split_pairs(pairs, 0.1, 0.1)
        train_generator = EmbeddingAlignmentGenerator(src_emb, tgt_emb, train_pairs)
        val_generator = EmbeddingAlignmentGenerator(src_emb, tgt_emb, val_pairs)
        test_generator = EmbeddingAlignmentGenerator(src_emb, tgt_emb, test_pairs)
        return train_generator, val_generator, test_generator
    
    def build_model(self, pretrained_model=None):
        self.args.input_size=300
        return super().build_model()

class CaptionTask(Task):
    trainer_cls = CaptionTrainer
    def build_dataset(self):
        if self.args.text_model == 'bert':
            tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")
            tokenize_fct = bert_tokenize_batch
            tokenize_args = (tokenizer,)
        elif self.args.text_model == 'ft':
            ft = fasttext.load_model(self.args.embed_path)
            tokenize_fct = fasttext_tokenize_batch
            tokenize_args = (ft,)

        if self.args.dataset.lower() == "coco":
            img_path = os.path.join(self.args.dataset_dir, "coco", "images")
            annotation_path = os.path.join(self.args.dataset_dir, "coco", "annotations")
            train_dataset, val_dataset, test_dataset = load_coco_data(img_path, annotation_path )
        elif self.args.dataset.lower() == "flickr30k":
            img_path = os.path.join(self.args.dataset_dir, "flickr30k", "images")
            annotation_path = os.path.join(self.args.dataset_dir, "flickr30k", "annotations.token")
            splits_path = os.path.join(self.args.dataset_dir, "flickr30k", "splits.json")
            train_dataset, val_dataset, test_dataset = load_flickr_data(img_path, annotation_path, splits_path)
        else:
            raise NotImplementedError("Supported datasets are CoCo and Flickr30k.")

        train_generator = CaptionGenerator(train_dataset, tokenize_fct, tokenize_args)
        val_generator = CaptionGenerator(val_dataset, tokenize_fct, tokenize_args)
        test_generator = CaptionGenerator(test_dataset, tokenize_fct, tokenize_args)
        return train_generator, val_generator, test_generator
    
    def build_model(self, pretrained_model=None):
        self.args.input_size = self.args.latent_size
        set_model = super().build_model()
        if self.args.text_model == 'bert':
            model = BertModel.from_pretrained("bert-base-uncased")
            text_encoder = BertEncoderWrapper(model)
        else:
            text_encoder = EmbeddingEncoderWrapper(self.args.embed_dim)

        if self.args.img_model == 'resnet':
            resnet = torchvision.models.resnet101(pretrained=True)
            resnet.fc = nn.Identity()
            img_encoder = ImageEncoderWrapper(resnet, 2048)
        else:
            enc = CONV_MODEL_BUILDERS[self.args.dataset](self.args)
            img_encoder = ImageEncoderWrapper(enc, self.args.latent_size)
        
        return MultiSetModel(set_model, img_encoder, text_encoder)


#
#   Counting Tasks
#

class CountingTask(Task):
    pretraining_task = ImageClassificationTask
    trainer_cls = CountingTrainer
    def build_dataset(self):
        if self.args.dataset.lower() == "mnist":
            trainval_dataset, test_dataset = load_mnist(self.args.dataset_dir)
            n_val = int(len(trainval_dataset) * self.args.val_split)
            train_dataset, val_dataset = torch.utils.data.random_split(trainval_dataset, [len(trainval_dataset)-n_val, n_val])
            generator_cls = ImageCooccurenceGenerator
        elif self.args.dataset.lower() == "omniglot":
            train_dataset, val_dataset, test_dataset = load_omniglot(self.args.dataset_dir)
            generator_cls = OmniglotCooccurenceGenerator
        elif self.args.dataset.lower() == "cifar100":
            trainval_dataset, test_dataset = load_cifar(self.args.dataset_dir)
            n_val = int(len(trainval_dataset) * self.args.val_split)
            train_dataset, val_dataset = torch.utils.data.random_split(trainval_dataset, [len(trainval_dataset)-n_val, n_val])
            generator_cls = CIFARCooccurenceGenerator
            train_dataset = DatasetByClass.splits(train_dataset, (100,))
            val_dataset = DatasetByClass.splits(val_dataset, (100,))
            test_dataset = DatasetByClass.splits(test_dataset, (100,))
        else:
            raise NotImplementedError("Supported datasets are MNIST, Omniglot and CIFAR100.")

        train_generator = generator_cls(train_dataset)
        val_generator = generator_cls(val_dataset)
        test_generator = generator_cls(test_dataset)
        return train_generator, val_generator, test_generator

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
        if self.args.dataset == 'omniglot':
            train_args['data_kwargs']['n_chars'] = 50
            eval_args['data_kwargs']['n_chars'] = 50
        return train_args, eval_args
    
    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'ss_schedule': self.args.ss_schedule,
            'poisson': self.args.poisson
        }
        return trainer_kwargs

    def build_model(self, pretrained_model=None):
        self.args.input_size = self.args.latent_size
        set_model = super().build_model()

        if pretrained_model == None:
            conv_encoder = CONV_MODEL_BUILDERS[self.args.dataset](self.args)
        else:
            conv_encoder = pretrained_model

        model = MultiSetImageModel(conv_encoder, set_model)
        return model


#
#   Distinguishability Tasks
#

class SyntheticDistinguishabilityTask(Task):

    def build_model(self, pretrained_model=None):
        self.args.input_size = self.args.n
        return super().build_model()

    def build_dataset(self):
        train_generator = DistinguishabilityGenerator()
        return train_generator, train_generator, train_generator
    

class MetaDatasetTask(Task):
    trainer_cls = MetaDatasetTrainer
    def build_model(self, pretrained_model=None):
        self.args.input_size = self.args.latent_size
        set_model = super().build_model()

        if self.args.img_encoder == "cnn":
            encoder = CONV_MODEL_BUILDERS[self.args.dataset](self.args)
        else:
            encoder = torchvision.models.resnet101(pretrained=False)
            encoder.fc = nn.Linear(2048, self.args.latent_size)
        discriminator = MultiSetImageModel(encoder, set_model)

        return model

    def build_dataset(self):
        image_size = 84 if self.args.img_encoder == "cnn" else 224
        train_generator = MetaDatasetGenerator(root_dir=self.args.dataset_path, image_size=image_size, split=Split.TRAIN)
        val_generator = MetaDatasetGenerator(root_dir=self.args.dataset_path, image_size=image_size, split=Split.VALID)
        test_generator = MetaDatasetGenerator(root_dir=self.args.dataset_path, image_size=image_size, split=Split.TEST)
        return train_generator, val_generator, test_generator

    def build_training_args(self):
        train_args, eval_args = super().build_trainer_args()
        train_args['data_kwargs']['p_dl'] = self.args.p_dl
        eval_args['data_kwargs']['p_dl'] = self.args.p_dl
        return train_args, eval_args

    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'save_every': self.args.save_every,
            'ss_schedule': self.args.ss_schedule,
            'episode_length': self.episode_length,
            'episode_classes': self.episode_classes,
            'episode_datasets': self.episode_datasets,
        }
        return trainer_kwargs


#
#   Statistical Distance Tasks
#

LOSSES = {
    'l1': nn.L1Loss,
    'mse': nn.MSELoss
}

class StatisticalDistanceTask(Task):
    trainer_cls = StatisticalDistanceTrainer

    def build_model(self, pretrained_model=None):
        self.args.input_size = self.args.n
        return super().build_model()

    def build_training_args(self):
        sample_kwargs = {
            'set_size': self.args.set_size, 
        }
        if self.args.dataset == 'gmm':
            sample_kwargs['nu']=5
            sample_kwargs['mu0']=0
            sample_kwargs['s0']=0.3

        if self.args.equi and self.args.vardim:
            dim_range = math.ceil(self.args.n/8)
            sample_kwargs['dims'] = (max(2,self.args.n-dim_range),self.args.n+dim_range)
        else:
            sample_kwargs['n'] = self.args.n

        train_args = {
            'batch_size': self.args.batch_size,
            'grad_steps': self.args.grad_steps,
            'sample_kwargs': sample_kwargs,
            'label_kwargs': {},
            'clip': getattr(self.args, 'clip', -1)
        }
        eval_args = {
            'batch_size': self.args.batch_size,
            'sample_kwargs': sample_kwargs,
            'label_kwargs': {}
        }
        return train_args, eval_args


class KLTask(StatisticalDistanceTask):
    def build_dataset(self):
        if self.args.dataset == 'gmm':
            generator = GaussianGenerator(num_outputs=2, variable_dim=self.args.equi, return_params=True, mixture=True)
        elif self.args.dataset == 'nf':
            generator = NFGenerator(32, 2, num_outputs=2, use_maf=False, variable_dim=self.args.equi, return_params=True)
        else:
            raise NotImplementedError("gmm or nf")
        return generator, None, None

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
        train_args['normalize'] = 'whiten'
        eval_args['normalize'] = 'whiten'
        return train_args, eval_args
    
    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'label_fct': kl_mc,
            'exact_loss': True,
            'criterion': nn.L1Loss(),
            'baselines': {'knn': kl_knn}
        }
        if getattr(self.args, 'criterion', None) is not None:
            trainer_kwargs['criterion'] = LOSSES[self.args.criterion]
        return trainer_kwargs
        
class MITask(StatisticalDistanceTask):
    def build_dataset(self):
        generator = CorrelatedGaussianGenerator(return_params=True, variable_dim=self.args.equi)
        return generator, generator, None

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
        train_args['normalize'] = 'none'
        eval_args['normalize'] = 'none'
        return train_args, eval_args
    
    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'label_fct': mi_corr_gaussian,
            'exact_loss': True,
            'criterion': nn.MSELoss(),
            'baselines': {'kraskov':kraskov_mi1}
        }
        if getattr(self.args, 'criterion', None) is not None:
            trainer_kwargs['criterion'] = LOSSES[self.args.criterion]
        return trainer_kwargs


class DVTask(StatisticalDistanceTask):
    trainer_cls=DonskerVaradhanTrainer

    def build_dataset(self):
        if self.args.dataset == 'gmm':
            generator = GaussianGenerator(num_outputs=2, variable_dim=self.args.equi, return_params=True, mixture=True)
        elif self.args.dataset == 'nf':
            generator = NFGenerator(32, 2, num_outputs=2, use_maf=False, variable_dim=self.args.equi, return_params=True)
        elif self.args.dataset == 'corr':
            generator = CorrelatedGaussianGenerator2(return_params=True, variable_dim=self.args.equi, max_rho=self.args.max_rho)
        else:
            raise NotImplementedError("gmm or nf")
        return generator, generator, None

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
        if self.args.split_inputs:
            train_args['sample_kwargs']['sample_groups']=2

        train_args['normalize'] = 'whiten'
        eval_args['normalize'] = 'whiten'
        return train_args, eval_args
    
    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'label_fct': kl_mc,
            'criterion': nn.L1Loss(),
            'split_inputs': self.args.split_inputs,
            'mode': 'kl'
        }
        if getattr(self.args, 'criterion', None) is not None:
            trainer_kwargs['criterion'] = LOSSES[self.args.criterion]
        return trainer_kwargs
    
    def _build_model_mst(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'num_blocks':self.args.num_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'decoder_layers': self.args.decoder_layers,
            'merge': 'concat',
            'weight_sharing': 'sym',     #IMPORTANT
        }
        set_model = MultiSetTransformerEncoder(self.args.n, self.args.n, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model
    
    def _build_model_encdec(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'enc_blocks':self.args.enc_blocks,
            'dec_blocks':self.args.dec_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'output_layers': self.args.decoder_layers,
            'merge': 'concat',
            'decoder_self_attn': self.args.decoder_self_attn
        }
        n = self.args.n * 2 if self.args.dataset == 'corr'else self.args.n
        set_model = MultiSetTransformerEncoderDecoder(n, n, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model
    
    def build_model(self, pretrained_model=None):
        return self._build_model_encdec()



class DVMITask(StatisticalDistanceTask):
    trainer_cls=DonskerVaradhanMITrainer

    def build_dataset(self):
        if self.args.dataset == 'corr':
            generator = CorrelatedGaussianGenerator(return_params=True, variable_dim=self.args.equi, max_rho=self.args.max_rho)
        elif self.args.dataset == 'mixture':
            generator = LabelledGaussianGenerator(return_params=True, variable_dim=self.args.equi)
        elif self.args.dataset == 'adult':
            generator = ProtectedDatasetGenerator.from_adult(return_params=True)
        elif self.args.dataset == 'adult-rand':
            model_kwargs={
                'in_features': 102,
                'hidden_dim': 100,
                'activation': nn.ReLU(),
            }
            generator = RandomEncoderGenerator.from_adult(model_kwargs, return_params=True, variable_dim=self.args.equi)
        else:
            raise NotImplementedError("corr or mixture")
        return generator, generator, None

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
      
        train_args['sample_kwargs']['sample_groups']=2 if self.args.split_inputs else 1
        eval_args['sample_kwargs']['sample_groups']=2 if self.args.split_inputs else 1
        train_args['normalize'] = self.args.normalize
        eval_args['normalize'] = self.args.normalize
        return train_args, eval_args
    
    def build_trainer_kwargs(self):

        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'criterion': nn.L1Loss(),
            'estimate_size': getattr(self.args, 'estimate_size', -1),
            'scale': getattr(self.args, 'scale', 'none'),
            'eps': getattr(self.args, 'eps', 1e-8),
            'model_type': self.args.dv_model,
            'split_inputs': self.args.split_inputs
        }
        if self.args.dataset == 'corr':
            trainer_kwargs['x_marginal'] = StandardGaussianGenerator()
            trainer_kwargs['y_marginal'] = StandardGaussianGenerator()
            trainer_kwargs['sample_marg'] = getattr(self.args, 'sample_marg', True)
            trainer_kwargs['label_fct'] = mi_corr_gaussian
        elif self.args.dataset == 'mixture':
            trainer_kwargs['x_marginal'] = None
            trainer_kwargs['y_marginal'] = None
            trainer_kwargs['sample_marg'] = False
            trainer_kwargs['label_fct'] = kl_mc_mixture
        elif self.args.dataset == 'adult':
            trainer_kwargs['x_marginal'] = None
            trainer_kwargs['y_marginal'] = None
            trainer_kwargs['sample_marg'] = False
            trainer_kwargs['label_fct'] = None
        elif self.args.dataset == 'adult-rand':
            trainer_kwargs['x_marginal'] = None
            trainer_kwargs['y_marginal'] = None
            trainer_kwargs['sample_marg'] = False
            trainer_kwargs['label_fct'] = None

        if getattr(self.args, 'criterion', None) is not None:
            trainer_kwargs['criterion'] = LOSSES[self.args.criterion]
        return trainer_kwargs
    
    def _build_model_mst(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'num_blocks':self.args.num_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'decoder_layers': self.args.decoder_layers,
            'merge': 'concat',
            'weight_sharing': 'sym',     #IMPORTANT?? Not sure if necessary or not for MI but probably helpful
            'merge_output_sets': True
        }
        if self.args.dataset == 'corr':
            x_size, y_size = self.args.n, self.args.n
        elif self.args.dataset == 'mixture' or self.args.dataset == 'adult-rand':
            x_size, y_size = self.args.n, 1
        elif self.args.dataset == 'adult':
            x_size, y_size = 102, 1
        set_model = MultiSetTransformerEncoder(x_size, y_size, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model

    def _build_model_encdec(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'enc_blocks':self.args.enc_blocks,
            'dec_blocks':self.args.dec_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'output_layers': self.args.decoder_layers,
            'merge': 'concat',
            'decoder_self_attn': self.args.decoder_self_attn
        }
        if self.args.dataset == 'corr':
            input_size = self.args.n * 2
        elif self.args.dataset == 'mixture' or self.args.dataset == 'adult-rand':
            input_size = self.args.n + 1
        elif self.args.dataset == 'adult':
            input_size = 102 + 1
        set_model = MultiSetTransformerEncoderDecoder(input_size, input_size, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model

    def build_model(self, pretrained_model=None):
        if self.args.dv_model == 'mst':
            return self._build_model_mst()
        else:
            return self._build_model_encdec()



#
#   DV2
#
'''
class DVTask2(StatisticalDistanceTask):
    trainer_cls=DonskerVaradhanTrainer2

    def build_dataset(self):
        if self.args.dataset == 'gmm':
            generator = GaussianGenerator(num_outputs=2, variable_dim=self.args.equi, return_params=True, mixture=True)
        elif self.args.dataset == 'nf':
            generator = NFGenerator(32, 2, num_outputs=2, use_maf=False, variable_dim=self.args.equi, return_params=True)
        elif self.args.dataset == 'corr':
            generator = CorrelatedGaussianGenerator(return_params=True, variable_dim=self.args.equi, max_rho=self.args.max_rho)
        elif self.args.dataset == 'corr2':
            generator = CorrelatedGaussianGenerator2(return_params=True, variable_dim=self.args.equi, max_rho=self.args.max_rho)
        else:
            raise NotImplementedError("gmm or nf")
        return generator, generator, None

    def build_training_args(self):
        train_args, eval_args = super().build_training_args()
        #if self.args.split_inputs:
            #train_args['sample_kwargs']['sample_groups']=2

        train_args['normalize'] = 'whiten'
        eval_args['normalize'] = 'whiten'
        return train_args, eval_args
    
    def build_trainer_kwargs(self):
        trainer_kwargs = {
            'eval_every': self.args.eval_every,
            'save_every': self.args.save_every,
            'criterion': nn.L1Loss(),
            'split_inputs': False,
            'dataset': self.args.dataset,
            'model_type': 'mst',
            'estimate_size': self.args.estimate_size,
            'model_type': self.args.dv_model
        }
        if self.args.dataset == 'corr':
            trainer_kwargs['mode'] = 'mi-kl'
            trainer_kwargs['label_fct'] = mi_corr_gaussian
        elif self.args.dataset == 'corr2':
            trainer_kwargs['mode'] = 'mi-kl'
            trainer_kwargs['label_fct'] = kl_mc
        else:
            trainer_kwargs['mode'] = 'kl'
            trainer_kwargs['label_fct'] = kl_mc   

        if getattr(self.args, 'criterion', None) is not None:
            trainer_kwargs['criterion'] = LOSSES[self.args.criterion]
        return trainer_kwargs
    
    def _build_model_mst(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'num_blocks':self.args.num_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'decoder_layers': self.args.decoder_layers,
            'merge': 'concat',
            'weight_sharing': 'sym',     #IMPORTANT
        }
        n = self.args.n * 2 if self.args.dataset == 'corr'else self.args.n
        set_model = MultiSetTransformerEncoder(n, n, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model
    
    def _build_model_encdec(self):
        model_kwargs={
            'ln':self.args.layer_norm,
            'remove_diag':False,
            'enc_blocks':self.args.enc_blocks,
            'dec_blocks':self.args.dec_blocks,
            'num_heads':self.args.num_heads,
            'dropout':self.args.dropout,
            'equi':self.args.equi,
            'output_layers': self.args.decoder_layers,
            'merge': 'concat',
            'decoder_self_attn': self.args.decoder_self_attn
        }
        set_model = MultiSetTransformerEncoderDecoder(self.args.n*2, self.args.n*2, self.args.latent_size, self.args.hidden_size, 1, **model_kwargs)
        return set_model
    
    
    def build_model(self, pretrained_model=None):
        if self.args.dv_model == 'encdec':
            return self._build_model_encdec()
        else:
            return self._build_model_mst()
'''


TASKS = {
    'counting': CountingTask,
    'align/caption': CaptionTask,
    'align/embed': EmbeddingTask,
    'dist/synthetic': SyntheticDistinguishabilityTask,
    'dist/meta-dataset': MetaDatasetTask,
    'stat/KL': KLTask,
    'stat/MI': MITask,
    'stat/DV': DVTask,
    'stat/DV-MI': DVMITask,
    #'stat/DV2': DVTask2
}



#### DV 2

